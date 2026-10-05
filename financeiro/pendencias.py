"""Fila de alteracoes que precisam ser repetidas na cobranca da Caixa.

Regras:
- So geram pendencia parcelas com boleto EM ABERTO no banco (nosso numero + situacao do extrato).
- Importar o extrato nunca gera pendencia (os dados vieram do proprio banco).
- Alterar e depois desfazer a mesma coisa cancela a pendencia; alterar duas vezes atualiza a mesma.
- Excluir a parcela ou marca-la como paga no sistema pede a BAIXA do boleto e cancela as outras pendencias dela.
- Alterar o cliente so gera pendencia se o cadastro ja estava completo (completar o cadastro
  pela primeira vez nao muda nada no banco).
"""

from contextvars import ContextVar
from datetime import date
from decimal import Decimal

from django.utils import timezone

from .extrato import EM_ABERTO
from .models import PendenciaCaixa
from .templatetags.financeiro_tags import brl

_usuario_atual = ContextVar("usuario_atual", default=None)


class UsuarioAtualMiddleware:
    """Guarda o usuario da requisicao para os signals saberem quem fez a alteracao."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _usuario_atual.set(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            _usuario_atual.reset(token)


def usuario_atual():
    usuario = _usuario_atual.get()
    return usuario if usuario is not None and usuario.is_authenticated else None


def aberta_no_banco(parcela) -> bool:
    return bool(parcela.nosso_numero) and parcela.situacao_banco == EM_ABERTO


def serializar(valor):
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    return valor


def _pendentes(parcela):
    # Pelo nosso numero (e nao pela FK): continua funcionando depois que a parcela e excluida.
    return PendenciaCaixa.objects.filter(nosso_numero=parcela.nosso_numero, status=PendenciaCaixa.Status.PENDENTE)


def dados_titulo(parcela) -> dict:
    """Dados do boleto que o arquivo de remessa precisa (guardados para o caso de a parcela ser excluida)."""
    fin = parcela.financiamento
    return {
        "vencimento": serializar(parcela.vencimento),
        "valor": serializar(parcela.valor),
        "documento": fin.numero_documento,
        "emissao": serializar(fin.data_entrada),
        "pagador": fin.cliente.dados_pagador(),
    }


def descricao(parcela) -> str:
    return f"{parcela.rotulo_numero} · venc. {parcela.vencimento:%d/%m/%Y} · {brl(parcela.valor)}"


def registrar(parcela, tipo, antes: dict, depois: dict, motivo="", vincular=True):
    antes = {k: serializar(v) for k, v in antes.items()}
    depois = {k: serializar(v) for k, v in depois.items()}
    existente = _pendentes(parcela).filter(tipo=tipo).first()
    if existente:
        if existente.antes == depois:  # voltou ao que era: nada a fazer no banco
            existente.status = PendenciaCaixa.Status.CANCELADA
            existente.motivo = "Alteração desfeita no sistema."
        else:
            existente.depois = depois
            existente.descricao_parcela = descricao(parcela)
            existente.titulo = dados_titulo(parcela)
        existente.save()
        return existente
    if antes == depois:
        return None
    return PendenciaCaixa.objects.create(
        parcela=parcela if vincular else None,
        nosso_numero=parcela.nosso_numero,
        cliente=parcela.financiamento.cliente.nome,
        descricao_parcela=descricao(parcela),
        tipo=tipo,
        motivo=motivo,
        antes=antes,
        depois=depois,
        titulo=dados_titulo(parcela),
        criado_por=usuario_atual(),
    )


def pedir_baixa(parcela, motivo, vincular=True):
    """Cancela as alteracoes pendentes da parcela (nao adianta alterar um boleto que vai ser baixado)."""
    _pendentes(parcela).exclude(tipo=PendenciaCaixa.Tipo.BAIXA).update(
        status=PendenciaCaixa.Status.CANCELADA, motivo="Substituída por pedido de baixa.", atualizado_em=timezone.now()
    )
    if _pendentes(parcela).filter(tipo=PendenciaCaixa.Tipo.BAIXA).exists():
        return
    registrar(
        parcela, PendenciaCaixa.Tipo.BAIXA, {"situacao": "EM ABERTO"}, {"situacao": "BAIXAR"}, motivo, vincular=vincular
    )


def parcela_alterada(parcela, antes: dict):
    if getattr(parcela, "_sem_pendencia", False) or not antes or not aberta_no_banco(parcela):
        return
    if antes["data_pagamento"] is None and parcela.data_pagamento is not None:
        pedir_baixa(parcela, f"Marcada como paga no sistema em {parcela.data_pagamento:%d/%m/%Y} (paga fora do boleto).")
        return
    if antes["data_pagamento"] is not None and parcela.data_pagamento is None:
        baixa = _pendentes(parcela).filter(tipo=PendenciaCaixa.Tipo.BAIXA).first()
        if baixa:
            baixa.status = PendenciaCaixa.Status.CANCELADA
            baixa.motivo = "Pagamento desmarcado no sistema."
            baixa.save()
    if parcela.data_pagamento is not None:
        return
    if antes["vencimento"] != parcela.vencimento:
        registrar(
            parcela, PendenciaCaixa.Tipo.ALTERAR_VENCIMENTO,
            {"vencimento": antes["vencimento"]}, {"vencimento": parcela.vencimento},
        )
    if antes["valor"] != parcela.valor:
        registrar(
            parcela, PendenciaCaixa.Tipo.ALTERAR_VALOR,
            {"valor": antes["valor"]}, {"valor": parcela.valor},
            "Se a Caixa não aceitar alterar o valor, baixe este boleto e emita um novo.",
        )


def parcela_excluida(parcela):
    """Chamada no post_delete: a parcela ja nao existe, entao a pendencia fica sem o vinculo (so o nosso numero)."""
    if aberta_no_banco(parcela):
        pedir_baixa(parcela, "Parcela excluída no sistema.", vincular=False)


def cliente_alterado(cliente, antes: dict, antes_completo: bool):
    if not antes_completo or antes == cliente.dados_pagador():
        return
    from .models import Parcela

    abertas = Parcela.objects.filter(
        financiamento__cliente=cliente, data_pagamento__isnull=True, nosso_numero__isnull=False, situacao_banco=EM_ABERTO
    ).select_related("financiamento__cliente")
    for parcela in abertas:
        registrar(parcela, PendenciaCaixa.Tipo.ALTERAR_PAGADOR, antes, cliente.dados_pagador())
