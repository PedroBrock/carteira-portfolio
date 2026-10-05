from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from financeiro.models import Atividade, Cliente, Financiamento, Parcela

HOJE = date(2026, 9, 15)


def criar_parcelas():
    fin = Financiamento.objects.create(cliente=Cliente.objects.create(nome="CLIENTE"), total_parcelas=10)
    dados = [
        # (numero, vencimento, valor, pago, verificar)
        (1, date(2026, 9, 5), "100.00", None, False),  # setembro: ja venceu, em aberto
        (2, date(2026, 9, 10), "200.00", date(2026, 9, 10), False),  # setembro: paga
        (3, date(2026, 9, 15), "300.00", None, False),  # setembro: vence hoje (a vencer)
        (4, date(2026, 9, 30), "400.00", None, False),  # setembro: a vencer
        (5, date(2026, 9, 20), "999.00", None, True),  # setembro: baixado - verificar (fora dos totais)
        (6, date(2026, 10, 5), "500.00", None, False),  # outubro
        (7, date(2026, 10, 31), "600.00", None, False),  # outubro
        (8, date(2026, 7, 1), "700.00", None, False),  # vencida (mais de 1 mes)
        (9, date(2026, 12, 1), "800.00", None, False),  # apos 60 dias
    ]
    for numero, venc, valor, pago, verificar in dados:
        Parcela.objects.create(
            financiamento=fin, numero=numero, vencimento=venc, valor=Decimal(valor),
            data_pagamento=pago, requer_verificacao=verificar,
        )


@mock.patch("financeiro.views.timezone.localdate", return_value=HOJE)
class RelatoriosTests(TestCase):
    def setUp(self):
        criar_parcelas()
        u = User.objects.create_user("func", password="senha-forte-123", is_staff=True)
        u.groups.add(Group.objects.get(name="Consulta"))
        self.client.login(username="func", password="senha-forte-123")

    def test_mes_atual(self, _):
        ctx = self.client.get(reverse("financeiro:relatorios")).context
        mes = ctx["mes"]
        self.assertEqual((mes["venceram"]["qtd"], mes["venceram"]["total"]), (1, Decimal("100.00")))
        self.assertEqual((mes["a_vencer"]["qtd"], mes["a_vencer"]["total"]), (2, Decimal("700.00")))
        self.assertEqual((mes["a_receber"]["qtd"], mes["a_receber"]["total"]), (3, Decimal("800.00")))
        self.assertEqual((mes["pagas"]["qtd"], mes["pagas"]["total"]), (1, Decimal("200.00")))
        self.assertEqual((mes["previsto"]["qtd"], mes["previsto"]["total"]), (4, Decimal("1000.00")))
        prox = ctx["proximo_mes"]
        self.assertEqual((prox["qtd"], prox["total"]), (2, Decimal("1100.00")))

    def test_carteira(self, _):
        cart = self.client.get(reverse("financeiro:relatorios")).context["carteira"]
        self.assertEqual(cart["em_carencia"]["qtd"], 1)  # 05/09 (venceu ha menos de 1 mes)
        self.assertEqual(cart["ate_30"]["qtd"], 3)  # 15/09 (hoje), 30/09, 05/10
        self.assertEqual(cart["ate_60"]["qtd"], 1)  # 31/10
        self.assertEqual(cart["apos_60"]["qtd"], 1)  # dezembro
        self.assertEqual(cart["vencidas"]["qtd"], 1)  # julho
        self.assertEqual(cart["total_aberto"]["qtd"], 7)

    def test_navegar_para_outro_mes(self, _):
        resp = self.client.get(reverse("financeiro:relatorios"), {"mes": "2026-10"})
        self.assertEqual(resp.context["mes"]["a_receber"]["qtd"], 2)
        self.assertEqual(resp.context["proximo_mes"]["qtd"], 0)
        self.assertContains(resp, "?mes=2026-09")
        self.assertContains(resp, "?mes=2026-11")

    def test_mes_invalido_volta_para_o_atual(self, _):
        resp = self.client.get(reverse("financeiro:relatorios"), {"mes": "abc"})
        self.assertEqual(resp.context["inicio"], date(2026, 9, 1))


class AtividadesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.func = User.objects.create_user("func", password="senha-forte-123", is_staff=True)
        self.func.groups.add(Group.objects.get(name="Financeiro"))

    def test_login_logout_e_falha_sao_registrados(self):
        self.client.post(reverse("admin:login"), {"username": "func", "password": "errada"})
        self.client.post(reverse("admin:login"), {"username": "func", "password": "senha-forte-123"})
        self.client.post(reverse("admin:logout"))
        tipos = list(Atividade.objects.order_by("data", "pk").values_list("tipo", "usuario_nome"))
        self.assertEqual(
            tipos,
            [
                (Atividade.Tipo.LOGIN_FALHOU, "func"),
                (Atividade.Tipo.LOGIN, "func"),
                (Atividade.Tipo.LOGOUT, "func"),
            ],
        )

    def test_so_admin_ve_a_tela(self):
        self.client.login(username="func", password="senha-forte-123")
        self.assertRedirects(self.client.get(reverse("financeiro:atividades")), reverse("financeiro:painel"))

    def test_acoes_de_todos_os_usuarios_na_mesma_tela(self):
        criar_parcelas()
        # funcionario marca pagas em massa e importa (acao registrada)
        self.client.login(username="func", password="senha-forte-123")
        ids = list(Parcela.objects.filter(numero__in=[1, 3]).values_list("pk", flat=True))
        self.client.post(
            reverse("admin:financeiro_parcela_changelist"),
            {"action": "marcar_pago_hoje", "_selected_action": ids},
        )
        self.assertEqual(LogEntry.objects.filter(user=self.func).count(), 2)
        self.client.logout()

        # admin exclui duas parcelas de uma vez (Django grava o historico em lote)
        self.client.login(username="admin", password="senha-forte-123")
        ids = list(Parcela.objects.filter(numero__in=[8, 9]).values_list("pk", flat=True))
        self.client.post(
            reverse("admin:financeiro_parcela_changelist"),
            {"action": "delete_selected", "_selected_action": ids, "post": "yes"},
        )

        resp = self.client.get(reverse("financeiro:atividades"))
        self.assertContains(resp, "Marcada como paga em", count=2)
        self.assertContains(resp, "class=\"tipo-EXCLUIU\"", count=2)
        self.assertContains(resp, "class=\"tipo-LOGIN\"")

        resp = self.client.get(reverse("financeiro:atividades"), {"usuario": self.func.pk, "tipo": "ALTEROU"})
        linhas = resp.context["pagina"].object_list
        self.assertEqual(len(linhas), 2)
        self.assertTrue(all(linha["usuario"] == "func" for linha in linhas))

    def test_importacao_registrada(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from financeiro.tests.test_importacao import EXTRATO

        self.client.login(username="func", password="senha-forte-123")
        arquivo = SimpleUploadedFile("extrato.txt", EXTRATO.encode("latin-1"))
        self.client.post(reverse("financeiro:importar_extrato"), {"arquivo": arquivo})
        imp = Atividade.objects.get(tipo=Atividade.Tipo.IMPORTACAO)
        self.assertEqual(imp.usuario, self.func)
        self.assertIn("extrato.txt", imp.descricao)
        self.assertIn("7 títulos lidos", imp.descricao)

    def test_ip_atras_do_proxy(self):
        self.client.post(
            reverse("admin:login"),
            {"username": "func", "password": "senha-forte-123"},
            HTTP_X_FORWARDED_FOR="200.100.50.25, 10.0.0.1",
        )
        self.assertEqual(Atividade.objects.get(tipo=Atividade.Tipo.LOGIN).ip, "200.100.50.25")
