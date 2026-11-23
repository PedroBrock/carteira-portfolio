from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from financeiro.models import ContaEscritorio, somar_meses

HOJE = date(2026, 9, 15)
LISTA = reverse("admin:financeiro_contaescritorio_changelist")


def criar_contas():
    return [
        ContaEscritorio.objects.create(
            descricao="Luz", categoria="ENERGIA", valor=Decimal("250.40"), vencimento=date(2026, 9, 10)
        ),
        ContaEscritorio.objects.create(
            descricao="Internet", categoria="INTERNET", valor=Decimal("120.00"), vencimento=date(2026, 9, 20)
        ),
        ContaEscritorio.objects.create(
            descricao="Aluguel", categoria="ALUGUEL", valor=Decimal("1500.00"), vencimento=date(2026, 9, 5),
            data_pagamento=date(2026, 9, 5),
        ),
    ]


def usuario(grupo):
    u = User.objects.create_user(grupo.lower(), password="senha-forte-123", is_staff=True)
    u.groups.add(Group.objects.get(name=grupo))
    return u


class SituacaoContaTests(TestCase):
    def test_situacao(self):
        luz, internet, aluguel = criar_contas()
        self.assertEqual(luz.situacao(HOJE), "ATRASADA")
        self.assertEqual(internet.situacao(HOJE), "EM_ABERTO")
        self.assertEqual(aluguel.situacao(HOJE), "PAGA")
        # No dia do vencimento ainda esta em aberto.
        self.assertEqual(internet.situacao(date(2026, 9, 20)), "EM_ABERTO")


@mock.patch("financeiro.admin.timezone.localdate", return_value=HOJE)
class ContasEscritorioAdminTests(TestCase):
    def setUp(self):
        self.luz, self.internet, self.aluguel = criar_contas()

    def entrar(self, grupo):
        self.user = usuario(grupo)
        self.client.force_login(self.user)

    def test_aba_no_cabecalho_e_totais(self, _):
        self.entrar("Consulta")
        resposta = self.client.get(reverse("admin:index"))
        self.assertContains(resposta, f'href="{LISTA}"')
        resposta = self.client.get(LISTA)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta.context["totais_contas"],
            {"total": Decimal("1870.40"), "pago": Decimal("1500.00"), "em_aberto": Decimal("370.40")},
        )

    def test_sem_permissao_nao_ve_a_aba(self, _):
        u = User.objects.create_user("semgrupo", password="senha-forte-123", is_staff=True)
        self.client.force_login(u)
        self.assertNotContains(self.client.get(reverse("admin:index")), f'href="{LISTA}"')

    def test_filtro_situacao(self, _):
        self.entrar("Consulta")
        for valor, esperado in [("ATRASADA", [self.luz]), ("EM_ABERTO", [self.internet]), ("PAGA", [self.aluguel])]:
            cl = self.client.get(LISTA, {"situacao": valor}).context["cl"]
            self.assertEqual(list(cl.queryset), esperado, valor)

    def test_financeiro_cadastra_e_marca_paga(self, _):
        self.entrar("Financeiro")
        resposta = self.client.post(
            reverse("admin:financeiro_contaescritorio_add"),
            {"descricao": "Telefone", "categoria": "TELEFONE", "valor": "89.90", "vencimento": "25/09/2026"},
        )
        self.assertEqual(resposta.status_code, 302, getattr(resposta, "context", None) and resposta.context["errors"])
        tel = ContaEscritorio.objects.get(descricao="Telefone")
        self.assertEqual(tel.valor, Decimal("89.90"))

        self.client.post(LISTA, {"action": "marcar_paga_hoje", "_selected_action": [self.luz.pk, self.aluguel.pk]})
        self.luz.refresh_from_db()
        self.aluguel.refresh_from_db()
        self.assertEqual(self.luz.data_pagamento, HOJE)
        self.assertEqual(self.aluguel.data_pagamento, date(2026, 9, 5))  # ja paga: nao muda
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.luz.pk), user=self.user).exists())

    def test_lancar_proximo_mes(self, _):
        self.entrar("Financeiro")
        self.client.post(LISTA, {"action": "lancar_proximo_mes", "_selected_action": [self.aluguel.pk]})
        nova = ContaEscritorio.objects.exclude(pk=self.aluguel.pk).get(descricao="Aluguel")
        self.assertEqual((nova.vencimento, nova.valor, nova.data_pagamento), (date(2026, 10, 5), Decimal("1500.00"), None))

    def test_financeiro_exclui(self, _):
        self.entrar("Financeiro")
        resposta = self.client.post(reverse("admin:financeiro_contaescritorio_delete", args=[self.luz.pk]), {"post": "yes"})
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(ContaEscritorio.objects.filter(pk=self.luz.pk).exists())
        # Exclusao em lote pela acao da lista, com registro no historico.
        self.client.post(
            LISTA, {"action": "delete_selected", "_selected_action": [self.internet.pk, self.aluguel.pk], "post": "yes"}
        )
        self.assertFalse(ContaEscritorio.objects.exists())
        self.assertEqual(LogEntry.objects.filter(user=self.user, action_flag=DELETION).count(), 3)

    def test_consulta_nao_exclui(self, _):
        self.entrar("Consulta")
        resposta = self.client.post(reverse("admin:financeiro_contaescritorio_delete", args=[self.luz.pk]), {"post": "yes"})
        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(ContaEscritorio.objects.filter(pk=self.luz.pk).exists())

    def test_consulta_nao_altera(self, _):
        self.entrar("Consulta")
        self.client.post(LISTA, {"action": "marcar_paga_hoje", "_selected_action": [self.luz.pk]})
        self.luz.refresh_from_db()
        self.assertIsNone(self.luz.data_pagamento)


class SomarMesesTests(TestCase):
    def test_fim_de_mes_nao_escorrega(self):
        self.assertEqual(somar_meses(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(somar_meses(date(2026, 1, 31), 2), date(2026, 3, 31))
        self.assertEqual(somar_meses(date(2026, 11, 10), 3), date(2027, 2, 10))


@mock.patch("financeiro.admin.timezone.localdate", return_value=HOJE)
class ContaParceladaTests(TestCase):
    def setUp(self):
        self.user = usuario("Financeiro")
        self.client.force_login(self.user)

    def lancar(self, **extra):
        dados = {
            "descricao": "Internet", "categoria": "INTERNET", "fornecedor": "Vivo", "valor": "99.90",
            "quantidade_parcelas": "12", "vencimento": "31/01/2027", "observacoes": "contrato 123",
        }
        return self.client.post(reverse("admin:financeiro_contaescritorio_add"), {**dados, **extra})

    def test_lanca_todas_as_parcelas(self, _):
        self.assertContains(self.client.get(reverse("admin:financeiro_contaescritorio_add")), "quantidade_parcelas")
        resposta = self.lancar()
        self.assertEqual(resposta.status_code, 302)
        contas = list(ContaEscritorio.objects.order_by("parcela"))
        self.assertEqual([c.parcela for c in contas], list(range(1, 13)))
        self.assertEqual({c.total_parcelas for c in contas}, {12})
        self.assertEqual({c.valor for c in contas}, {Decimal("99.90")})
        self.assertEqual({(c.fornecedor, c.observacoes) for c in contas}, {("Vivo", "contrato 123")})
        self.assertEqual(
            [c.vencimento for c in contas[:3]], [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]
        )
        self.assertEqual(contas[-1].vencimento, date(2027, 12, 31))
        self.assertEqual(str(contas[2]), "Internet (3/12) - 31/03/2027")
        self.assertEqual(LogEntry.objects.filter(user=self.user).count(), 12)

    def test_so_a_primeira_fica_paga(self, _):
        self.lancar(quantidade_parcelas="3", data_pagamento="31/01/2027")
        pagas = [c.data_pagamento for c in ContaEscritorio.objects.order_by("parcela")]
        self.assertEqual(pagas, [date(2027, 1, 31), None, None])

    def test_conta_simples_nao_vira_parcela(self, _):
        self.lancar(quantidade_parcelas="1")
        conta = ContaEscritorio.objects.get()
        self.assertEqual((conta.parcela, conta.total_parcelas), (None, None))

    def test_quantidade_invalida(self, _):
        self.assertEqual(self.lancar(quantidade_parcelas="0").status_code, 200)
        self.assertFalse(ContaEscritorio.objects.exists())

    def test_editar_parcela_mostra_numero_e_nao_cria_outras(self, _):
        self.lancar(quantidade_parcelas="2")
        segunda = ContaEscritorio.objects.get(parcela=2)
        url = reverse("admin:financeiro_contaescritorio_change", args=[segunda.pk])
        self.assertContains(self.client.get(url), "Total de parcelas")
        resposta = self.client.post(url, {
            "descricao": "Internet", "categoria": "INTERNET", "valor": "120.00", "vencimento": "28/02/2027",
        })
        self.assertEqual(resposta.status_code, 302)
        segunda.refresh_from_db()
        self.assertEqual((segunda.valor, segunda.parcela, segunda.total_parcelas), (Decimal("120.00"), 2, 2))
        self.assertEqual(ContaEscritorio.objects.count(), 2)
