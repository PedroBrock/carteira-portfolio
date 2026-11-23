from django.contrib.auth.models import Group, User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse


class MenuPrincipalTests(TestCase):
    def entrar(self, grupo=None):
        if grupo:
            u = User.objects.create_user("func", password="senha-forte-123", is_staff=True)
            u.groups.add(Group.objects.get(name=grupo))
        else:
            User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="func" if grupo else "admin", password="senha-forte-123")

    def test_superusuario_ve_todos_os_itens_e_o_atual_fica_marcado(self):
        self.entrar()
        resp = self.client.get(reverse("financeiro:relatorios"))
        for rotulo in ["Painel", "Parcelas", "Clientes", "Financiamentos", "Pendências Caixa",
                       "Contas do escritório", "Relatórios", "Importar extrato", "Atividades", "Todos os cadastros"]:
            self.assertContains(resp, f">{rotulo}</a></li>", msg_prefix=rotulo)
        self.assertContains(resp, f'class="atual" aria-current="page"><a href="{reverse("financeiro:relatorios")}">', count=1)

    def test_paginas_do_admin_marcam_o_cadastro_certo(self):
        self.entrar()
        url = reverse("admin:financeiro_parcela_changelist")
        self.assertContains(self.client.get(url), f'aria-current="page"><a href="{url}">Parcelas</a>')
        # Paginas sem item proprio no menu (ex.: remessas) ficam em "Todos os cadastros"
        resp = self.client.get(reverse("admin:financeiro_remessacaixa_changelist"))
        self.assertContains(resp, f'aria-current="page"><a href="{reverse("admin:index")}">Todos os cadastros</a>')

    def test_grupo_consulta_nao_ve_importar_nem_atividades(self):
        self.entrar("Consulta")
        resp = self.client.get(reverse("financeiro:painel"))
        self.assertContains(resp, ">Parcelas</a></li>")
        self.assertNotContains(resp, ">Importar extrato</a>")
        self.assertNotContains(resp, ">Atividades</a>")

    def test_barra_lateral_do_admin_desligada(self):
        self.entrar()
        self.assertNotContains(self.client.get(reverse("admin:index")), 'id="nav-sidebar"')


class PainelTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")

    def test_totais_levam_para_a_lista_de_parcelas_filtrada(self):
        resp = self.client.get(reverse("financeiro:painel"))
        lista = reverse("admin:financeiro_parcela_changelist")
        for situacao in ["A_VENCER", "VENCIDO", "VERIFICAR"]:
            self.assertContains(resp, f'href="{lista}?situacao={situacao}"')
        self.assertContains(resp, f'href="{lista}?data_pagamento__year=')
        # O filtro por mes de pagamento e aceito pelo admin
        self.assertEqual(self.client.get(f"{lista}?data_pagamento__year=2026&data_pagamento__month=10").status_code, 200)

    def test_tabelas_vazias_explicam_o_que_falta(self):
        self.assertContains(self.client.get(reverse("financeiro:painel")), "Nenhuma parcela vencida.")


class FontesTests(TestCase):
    def test_fontes_do_tema_sao_servidas_pelo_proprio_site(self):
        for peso in ["barlow-400", "barlow-500", "barlow-600", "barlow-700",
                     "barlow-semi-condensed-500", "barlow-semi-condensed-600", "barlow-semi-condensed-700"]:
            self.assertTrue(finders.find(f"financeiro/fontes/{peso}.woff2"), peso)


class MenuLateralEPainelPorMesTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")

    def test_menu_lateral_em_grupos_com_contador_de_pendencias(self):
        from datetime import date

        from financeiro.models import Cliente, Financiamento, Parcela, PendenciaCaixa

        f = Financiamento.objects.create(cliente=Cliente.objects.create(nome="Ana"), total_parcelas=1)
        p = Parcela.objects.create(financiamento=f, numero=1, nosso_numero="14000000000000001", valor=100, vencimento=date(2030, 1, 10))
        PendenciaCaixa.objects.create(parcela=p, nosso_numero=p.nosso_numero, cliente="Ana", tipo=PendenciaCaixa.Tipo.choices[0][0])
        resp = self.client.get(reverse("financeiro:painel"))
        self.assertContains(resp, 'class="lateral" id="menu-lateral"')
        self.assertContains(resp, 'aria-controls="menu-lateral"')
        for grupo in ["Carteira", "Caixa", "Escritório", "Sistema"]:
            self.assertContains(resp, f">{grupo}</p>", msg_prefix=grupo)
        self.assertContains(resp, 'data-icone="pendencias" data-qtd="1"')

    def test_tela_de_entrar_continua_sem_menu_lateral(self):
        self.client.logout()
        resp = self.client.get(reverse("admin:login"))
        self.assertContains(resp, '<header id="header">')
        self.assertNotContains(resp, "menu-lateral")

    def test_a_receber_agrupado_por_mes_com_total(self):
        from datetime import date
        from decimal import Decimal
        from unittest import mock

        from financeiro.models import Cliente, Financiamento, Parcela

        f = Financiamento.objects.create(cliente=Cliente.objects.create(nome="Bruno"), total_parcelas=4)
        for n, venc in enumerate([date(2026, 11, 20), date(2026, 12, 5), date(2026, 12, 20), date(2027, 2, 1)], 1):
            Parcela.objects.create(financiamento=f, numero=n, valor=Decimal("100.50"), vencimento=venc)
        with mock.patch("django.utils.timezone.localdate", return_value=date(2026, 11, 5)):
            resp = self.client.get(reverse("financeiro:painel"))
        meses = resp.context["meses_a_receber"]
        # janeiro sem parcela nao aparece; fevereiro fica fora (so o mes atual e os dois seguintes)
        self.assertEqual([m["mes"] for m in meses], [date(2026, 11, 1), date(2026, 12, 1)])
        self.assertEqual(meses[1]["total"], Decimal("201.00"))
        self.assertContains(resp, "Dezembro de 2026")
        self.assertContains(resp, "R$ 201,00")

    def test_logo_no_menu_no_login_e_no_icone_da_aba(self):
        resp = self.client.get(reverse("financeiro:painel"))
        self.assertContains(resp, 'class="logo"', count=2)  # menu lateral e barra do topo do celular
        self.assertContains(resp, "financeiro/favicon.svg")
        self.client.logout()
        self.assertContains(self.client.get(reverse("admin:login")), 'class="logo logo-completa"')
        self.assertNotContains(resp, "{%")
        self.assertNotContains(resp, "{#")
