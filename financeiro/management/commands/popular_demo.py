from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from financeiro.demo import CONVENIO_DEMO, EMPREENDIMENTOS, formatar_extrato, gerar_demo
from financeiro.extrato import EM_ABERTO, ler_extrato
from financeiro.importacao import importar_titulos
from financeiro.models import (
    Atividade, Cliente, ConvenioCaixa, Financiamento, Parcela, PendenciaCaixa, RemessaCaixa, TituloExcluido,
)
from financeiro.pendencias import _usuario_atual
from financeiro.remessa import gerar_remessa


class Command(BaseCommand):
    help = "Popula o sistema com dados fictícios para demonstração (clientes, contratos, boletos e pendências)."

    def add_arguments(self, parser):
        parser.add_argument("--limpar", action="store_true", help="Apaga os dados financeiros antes de popular.")
        parser.add_argument("--clientes", type=int, default=18)
        parser.add_argument("--usuario", default="demo", help="Superusuário da demonstração (padrão: demo).")
        parser.add_argument("--senha", default="demo", help="Senha do superusuário da demonstração (padrão: demo).")
        parser.add_argument(
            "--so-extrato", metavar="ARQUIVO",
            help="Só grava um extrato fictício no formato da Caixa (para testar a tela Importar extrato).",
        )

    def handle(self, *args, **opts):
        hoje = timezone.localdate()
        clientes, titulos, rng = gerar_demo(hoje, opts["clientes"])

        if opts["so_extrato"]:
            with open(opts["so_extrato"], "w", encoding="latin-1", newline="") as f:
                f.write(formatar_extrato(titulos))
            self.stdout.write(self.style.SUCCESS(f"Extrato fictício com {len(titulos)} títulos em {opts['so_extrato']}."))
            return

        if Parcela.objects.exists() and not opts["limpar"]:
            raise CommandError("O banco já tem parcelas. Use --limpar para apagar e popular de novo.")

        with transaction.atomic():
            if opts["limpar"]:
                # Parcelas primeiro: excluir parcela gera pedido de baixa e titulo excluido (signals).
                for modelo in (Parcela, PendenciaCaixa, RemessaCaixa, TituloExcluido, Financiamento, Cliente, Atividade):
                    modelo.objects.all().delete()
                LogEntry.objects.all().delete()
            admin, equipe = self._usuarios(opts["usuario"], opts["senha"])
            self._popular(hoje, clientes, titulos, rng, admin, equipe)

        self.stdout.write(self.style.SUCCESS(
            f"{Cliente.objects.count()} clientes, {Financiamento.objects.count()} contratos, "
            f"{Parcela.objects.count()} parcelas e {PendenciaCaixa.objects.count()} pendências para a Caixa."
        ))
        self.stdout.write(f"Entre em /admin/ com o usuário '{opts['usuario']}' e a senha '{opts['senha']}'.")

    def _usuarios(self, usuario, senha):
        admin, _ = User.objects.get_or_create(username=usuario, defaults={"email": f"{usuario}@exemplo.com"})
        admin.is_staff = admin.is_superuser = True
        admin.first_name = "Demonstração"
        admin.set_password(senha)
        admin.save()
        equipe = []
        for nome, grupo in [("ana.financeiro", "Financeiro"), ("bruno.consulta", "Consulta")]:
            u, criado = User.objects.get_or_create(username=nome, defaults={"is_staff": True})
            if criado:
                u.set_unusable_password()
                u.save()
            g = Group.objects.filter(name=grupo).first()
            if g:
                u.groups.add(g)
            equipe.append(u)
        return admin, equipe

    def _popular(self, hoje, clientes, titulos, rng, admin, equipe):
        importar_titulos(ler_extrato(formatar_extrato(titulos)), hoje)

        # Cadastro do pagador (dados do boleto); alguns ficam incompletos de proposito.
        dados = {c["nome"]: c for c in clientes}
        for i, cliente in enumerate(Cliente.objects.all()):
            if i % 6 == 5:
                continue
            for campo, valor in dados[cliente.nome].items():
                setattr(cliente, campo, valor)
            cliente.save()

        for fin in Financiamento.objects.exclude(total_parcelas=None):
            fin.descricao = f"{rng.choice(EMPREENDIMENTOS)} · Quadra {rng.choice('ABCDE')}, Lote {rng.randint(1, 40)}"
            fin.save()
        for fin in Financiamento.objects.filter(total_parcelas=None):
            fin.descricao = "Entrada"
            fin.save()

        ConvenioCaixa.objects.update_or_create(
            pk=ConvenioCaixa.objects.values_list("pk", flat=True).first(),
            defaults={
                **CONVENIO_DEMO, "juros_codigo": "2", "juros_valor": Decimal("1.00"),
                "multa_codigo": "2", "multa_valor": Decimal("2.00"), "ambiente_teste": True,
            },
        )

        # Alteracoes feitas no sistema em boletos em aberto viram pendencias para a Caixa.
        token = _usuario_atual.set(equipe[0])
        try:
            abertas = list(
                Parcela.objects.filter(
                    situacao_banco=EM_ABERTO, data_pagamento=None, vencimento__gt=hoje + timedelta(days=5),
                    financiamento__cliente__cpf_cnpj__gt="",
                ).select_related("financiamento__cliente")
            )
            rng.shuffle(abertas)
            prorrogada, desconto, paga_em_dinheiro, renegociada = abertas[:4]
            prorrogada.vencimento += timedelta(days=15)
            prorrogada.save()
            desconto.valor = (desconto.valor * Decimal("0.95")).quantize(Decimal("0.01"))
            desconto.save()
            paga_em_dinheiro.data_pagamento = hoje - timedelta(days=1)
            paga_em_dinheiro.save()
            renegociada.vencimento += timedelta(days=30)
            renegociada.save()
        finally:
            _usuario_atual.reset(token)

        remessa, _ = gerar_remessa(PendenciaCaixa.objects.filter(parcela=prorrogada), usuario=admin)

        tipo_parcela = ContentType.objects.get_for_model(Parcela)
        for parcela, texto in [
            (prorrogada, "Alterou vencimento."),
            (desconto, "Alterou valor."),
            (paga_em_dinheiro, "Alterou data de pagamento."),
            (renegociada, "Alterou vencimento."),
        ]:
            LogEntry.objects.create(
                user=equipe[0], content_type=tipo_parcela, object_id=str(parcela.pk),
                object_repr=str(parcela)[:200], action_flag=CHANGE, change_message=texto,
            )

        agora = timezone.now()
        for minutos, usuario, tipo, descricao in [
            (300, admin, Atividade.Tipo.LOGIN, ""),
            (295, admin, Atividade.Tipo.IMPORTACAO, f"Arquivo 'extrato_exemplo.txt': {len(titulos)} títulos lidos."),
            (120, None, Atividade.Tipo.LOGIN_FALHOU, "Tentativa com o usuário 'ana'"),
            (118, equipe[0], Atividade.Tipo.LOGIN, ""),
            (60, equipe[1], Atividade.Tipo.LOGIN, ""),
            (45, equipe[1], Atividade.Tipo.LOGOUT, ""),
        ]:
            a = Atividade.objects.create(
                usuario=usuario, usuario_nome=usuario.username if usuario else "ana", tipo=tipo,
                descricao=descricao, ip="203.0.113.10",
            )
            Atividade.objects.filter(pk=a.pk).update(data=agora - timedelta(minutes=minutos))
        if remessa:
            self.stdout.write(f"Remessa de teste nº {remessa.nsa} gerada com {remessa.quantidade} título(s).")
