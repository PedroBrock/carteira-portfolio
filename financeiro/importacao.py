"""Importa/atualiza clientes, financiamentos e parcelas a partir do extrato do banco.

A importacao e idempotente: a parcela e identificada pelo nosso numero, entao
o mesmo extrato (ou um extrato mais novo) pode ser importado varias vezes.
"""

from collections import Counter
from datetime import date

from django.db import transaction

from .extrato import BAIXADO_DEVOLUCAO, EM_ABERTO, LIQUIDADO, TituloExtrato
from .models import Cliente, Financiamento, Parcela, PendenciaCaixa, TituloExcluido
from .regras import um_mes_depois


@transaction.atomic
def importar_titulos(titulos: list[TituloExtrato], hoje: date) -> Counter:
    stats = Counter()
    clientes: dict[str, Cliente] = {}
    financiamentos: dict[tuple, Financiamento] = {}
    excluidos = set(TituloExcluido.objects.values_list("nosso_numero", flat=True))

    for t in titulos:
        pendentes = {
            p.tipo: p
            for p in PendenciaCaixa.objects.filter(nosso_numero=t.nosso_numero, status__in=ABERTAS).order_by("criado_em")
        }
        if pendentes:
            stats["pendencias_resolvidas"] += _resolver_pendencias(pendentes, t)

        if t.nosso_numero in excluidos:
            # Parcela excluida no sistema (ex.: erro de lancamento): nao recriar.
            stats["titulos_excluidos_ignorados"] += 1
            continue

        cliente = clientes.get(t.nome)
        if cliente is None:
            cliente, criado = Cliente.objects.get_or_create(nome=t.nome)
            clientes[t.nome] = cliente
            stats["clientes_novos"] += criado

        # Parcelas do mesmo contrato compartilham documento + total de parcelas.
        # Titulos sem numero de parcela (entradas, intermediarias) ficam num grupo "avulsos".
        chave = (cliente.pk, t.documento, t.total_parcelas)
        fin = financiamentos.get(chave)
        if fin is None:
            fin, criado = Financiamento.objects.get_or_create(
                cliente=cliente,
                numero_documento=t.documento,
                total_parcelas=t.total_parcelas,
                defaults={"data_entrada": t.data_entrada},
            )
            financiamentos[chave] = fin
            stats["financiamentos_novos"] += criado

        parcela = Parcela.objects.filter(nosso_numero=t.nosso_numero).first()
        criada = parcela is None
        if criada:
            parcela = Parcela(nosso_numero=t.nosso_numero, financiamento=fin)
        mudou_situacao = parcela.situacao_banco != t.situacao

        parcela.numero = t.parcela
        # Alteracao feita no sistema e ainda nao aplicada no banco: o extrato nao pode desfaze-la.
        if PendenciaCaixa.Tipo.ALTERAR_VALOR not in pendentes:
            parcela.valor = t.valor
        if PendenciaCaixa.Tipo.ALTERAR_VENCIMENTO not in pendentes:
            parcela.vencimento = t.vencimento
        parcela.situacao_banco = t.situacao
        parcela.ultimo_comando_banco = t.ultimo_comando

        if t.situacao == LIQUIDADO and not parcela.data_pagamento:
            # No extrato, o "ultimo comando" de um titulo liquidado e a data da liquidacao.
            parcela.data_pagamento = t.ultimo_comando
        elif t.situacao == BAIXADO_DEVOLUCAO and mudou_situacao and hoje <= um_mes_depois(t.vencimento):
            # Baixado antes de completar 1 mes do vencimento: nao e atraso, precisa de analise.
            parcela.requer_verificacao = True

        parcela._sem_pendencia = True  # dados vieram do banco: nao gerar pendencia para a Caixa
        parcela.save()
        stats["parcelas_novas" if criada else "parcelas_atualizadas"] += 1

    return stats


# Pendente ou ja enviada por remessa: enquanto o extrato nao mostrar a alteracao, ela continua valendo.
ABERTAS = [PendenciaCaixa.Status.PENDENTE, PendenciaCaixa.Status.ENVIADA]


def _resolver_pendencias(pendentes: dict, t: TituloExtrato) -> int:
    """Confirma/cancela pendencias que o extrato mostra como ja resolvidas no banco.

    Remove de `pendentes` as que foram resolvidas (o que sobra continua valendo sobre o extrato).
    """
    resolvidas = []
    for tipo, p in pendentes.items():
        if t.situacao != EM_ABERTO:
            if tipo == PendenciaCaixa.Tipo.BAIXA:
                p.status, p.motivo = PendenciaCaixa.Status.CONFIRMADA, f"Título {t.situacao.lower()} no banco."
            else:
                p.status, p.motivo = PendenciaCaixa.Status.CANCELADA, f"Título já está {t.situacao.lower()} no banco."
        elif tipo == PendenciaCaixa.Tipo.ALTERAR_VENCIMENTO and p.depois.get("vencimento") == t.vencimento.isoformat():
            p.status = PendenciaCaixa.Status.CONFIRMADA
        elif tipo == PendenciaCaixa.Tipo.ALTERAR_VALOR and p.depois.get("valor") == f"{t.valor:.2f}":
            p.status = PendenciaCaixa.Status.CONFIRMADA
        else:
            continue
        p.save()
        resolvidas.append(tipo)
    for tipo in resolvidas:
        del pendentes[tipo]
    return len(resolvidas)
