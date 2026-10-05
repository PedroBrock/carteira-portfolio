from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User

from financeiro.extrato import ExtratoInvalido, ler_extrato
from financeiro.importacao import importar_titulos
from financeiro.models import Cliente, Financiamento, Parcela
from financeiro.regras import Situacao

HOJE = date(2026, 9, 29)

EXTRATO = """CONSULTA DE TITULOS
--------------------------------------------------------------------------------
NOSSO NUMERO          NUM.DOCUMENTO      NOME DO SACADO                              VALOR DO TITULO    DT.VENCTO     SITUACAO                                 ULTIMO COMANDO    DT.ENTRADA
--------------------------------------------------------------------------------
 14000000000020010    001    /002/014    LUCAS HENRIQUE MOREIRA                             1.180,45    20.09.2025    LIQUIDADO                                22.09.2025        21.07.2025
 14000000000020011    001    /003/014    LUCAS HENRIQUE MOREIRA                             1.180,45    20.10.2026    EM ABERTO                                21.07.2025        21.07.2025
 14000000000030004    001    /004/010    FERNANDA LIMA BARBOSA                              1.450,00    10.08.2025    BAIXADO POR DEVOLUCAO                    09.09.2025        29.04.2025
 14000000000040013    001    /013/013    MARIA CLARA FONSECA                                1.734,20    30.08.2026    EM ABERTO                                02.07.2025        02.07.2025
 14000000000040900    001                MARIA CLARA FONSECA                                3.800,00    15.05.2026    BAIXADO POR DEVOLUCAO                    15.06.2026        02.07.2025
 14000000000050015    001    /015/017    PATRICIA DE SOUZA CAVALCANTI D                     3.310,90    18.04.2027    BAIXADO POR DEVOLUCAO                    29.01.2026        28.01.2026
 14000000000060027    001S27             RAFAEL NUNES DE ARAUJO PEREIRA                       712,30    30.01.2026    LIQUIDADO                                30.01.2026        29.01.2026
"""


class LerExtratoTests(TestCase):
    def test_le_todos_os_titulos(self):
        titulos = ler_extrato(EXTRATO)
        self.assertEqual(len(titulos), 7)
        t = titulos[0]
        self.assertEqual(t.nosso_numero, "14000000000020010")
        self.assertEqual((t.parcela, t.total_parcelas), (2, 14))
        self.assertEqual(t.nome, "LUCAS HENRIQUE MOREIRA")
        self.assertEqual(t.valor, Decimal("1180.45"))
        self.assertEqual(t.vencimento, date(2025, 9, 20))
        self.assertEqual(t.situacao, "LIQUIDADO")

    def test_titulo_avulso_e_documento_alfanumerico(self):
        titulos = ler_extrato(EXTRATO)
        self.assertIsNone(titulos[4].parcela)
        self.assertEqual(titulos[6].documento, "001S27")
        self.assertEqual(titulos[6].valor, Decimal("712.30"))

    def test_linha_de_titulo_quebrada_gera_erro(self):
        with self.assertRaises(ExtratoInvalido):
            ler_extrato(" 14000000000020010    001    linha estranha\n")


class ImportacaoTests(TestCase):
    def importar(self, texto=EXTRATO, hoje=HOJE):
        return importar_titulos(ler_extrato(texto), hoje)

    def situacoes(self):
        return dict(Parcela.objects.com_situacao(HOJE).values_list("nosso_numero", "situacao"))

    def test_situacoes_apos_importacao(self):
        self.importar()
        self.assertEqual(
            self.situacoes(),
            {
                "14000000000020010": Situacao.PAGO,
                "14000000000020011": Situacao.A_VENCER,
                "14000000000030004": Situacao.VENCIDO,
                "14000000000040013": Situacao.A_VENCER,  # venceu ha menos de 1 mes
                "14000000000040900": Situacao.VENCIDO,
                "14000000000050015": Situacao.VERIFICAR,  # baixado antes de vencer
                "14000000000060027": Situacao.PAGO,
            },
        )

    def test_anotacao_do_banco_bate_com_regra_em_python(self):
        self.importar()
        for p in Parcela.objects.com_situacao(HOJE):
            self.assertEqual(p.situacao, p.calcular_situacao(HOJE), p.nosso_numero)

    def test_liquidado_usa_ultimo_comando_como_data_de_pagamento(self):
        self.importar()
        self.assertEqual(Parcela.objects.get(nosso_numero="14000000000020010").data_pagamento, date(2025, 9, 22))

    def test_agrupa_clientes_e_financiamentos(self):
        self.importar()
        self.assertEqual(Cliente.objects.count(), 5)
        maria = Cliente.objects.get(nome="MARIA CLARA FONSECA")
        self.assertEqual(
            sorted(maria.financiamentos.values_list("total_parcelas", flat=True), key=lambda x: x or 0), [None, 13]
        )
        self.assertEqual(Financiamento.objects.get(cliente__nome__startswith="LUCAS").parcelas.count(), 2)

    def test_reimportar_nao_duplica_e_preserva_ajustes_manuais(self):
        self.importar()
        p = Parcela.objects.get(nosso_numero="14000000000050015")
        p.requer_verificacao = False
        p.save()
        pago_em_dinheiro = Parcela.objects.get(nosso_numero="14000000000020011")
        pago_em_dinheiro.data_pagamento = date(2026, 9, 1)
        pago_em_dinheiro.save()

        stats = self.importar()

        self.assertEqual(stats["parcelas_novas"], 0)
        self.assertEqual(Parcela.objects.count(), 7)
        self.assertFalse(Parcela.objects.get(nosso_numero="14000000000050015").requer_verificacao)
        self.assertEqual(Parcela.objects.get(nosso_numero="14000000000020011").data_pagamento, date(2026, 9, 1))

    def test_extrato_novo_registra_pagamento(self):
        self.importar()
        novo = EXTRATO.replace(
            "20.10.2026    EM ABERTO                                21.07.2025",
            "20.10.2026    LIQUIDADO                                15.10.2026",
        )
        self.importar(novo, hoje=date(2026, 10, 16))
        self.assertEqual(Parcela.objects.get(nosso_numero="14000000000020011").data_pagamento, date(2026, 10, 15))

    def test_comando_de_gerenciamento(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="latin-1", delete=False) as f:
            f.write(EXTRATO)
        call_command("importar_extrato", f.name, stdout=open("/dev/null", "w"))
        self.assertEqual(Parcela.objects.count(), 7)


class PainelTests(TestCase):
    def test_painel_exige_login_e_renderiza(self):
        self.assertEqual(self.client.get(reverse("financeiro:painel")).status_code, 302)
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        resp = self.client.get(reverse("financeiro:painel"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PATRICIA")
        for nome in ["cliente", "financiamento", "parcela"]:
            self.assertEqual(self.client.get(reverse(f"admin:financeiro_{nome}_changelist")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:financeiro_parcela_changelist"), {"situacao": "VENCIDO"}).status_code, 200
        )


class ImportarPeloNavegadorTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        self.url = reverse("financeiro:importar_extrato")

    def enviar(self, conteudo: str):
        from django.core.files.uploadedfile import SimpleUploadedFile

        arquivo = SimpleUploadedFile("extrato.txt", conteudo.encode("latin-1"), content_type="text/plain")
        return self.client.post(self.url, {"arquivo": arquivo}, follow=True)

    def test_formulario_abre(self):
        self.assertContains(self.client.get(self.url), "CONSULTA DE TITULOS")

    def test_upload_importa_e_volta_ao_painel(self):
        resp = self.enviar(EXTRATO)
        self.assertRedirects(resp, reverse("financeiro:painel"))
        self.assertContains(resp, "7 títulos lidos")
        self.assertEqual(Parcela.objects.count(), 7)

    def test_arquivo_invalido_mostra_erro_sem_importar(self):
        resp = self.enviar("qualquer coisa\n")
        self.assertContains(resp, "Nenhum título encontrado")
        self.assertEqual(Parcela.objects.count(), 0)

    def test_usuario_sem_login_nao_importa(self):
        self.client.logout()
        self.assertEqual(self.enviar(EXTRATO).redirect_chain[0][1], 302)
        self.assertEqual(Parcela.objects.count(), 0)


class SaudeTests(TestCase):
    def test_saude_responde_sem_login(self):
        resp = self.client.get(reverse("financeiro:saude"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"ok")


class ExclusaoDeParcelaTests(TestCase):
    PATRICIA = "14000000000050015"

    def setUp(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)

    def test_excluir_parcela_lembra_o_titulo(self):
        from financeiro.models import TituloExcluido

        Parcela.objects.filter(nosso_numero=self.PATRICIA).delete()
        registro = TituloExcluido.objects.get(nosso_numero=self.PATRICIA)
        self.assertEqual(registro.cliente, "PATRICIA DE SOUZA CAVALCANTI D")
        self.assertIn("15/17", registro.descricao)

    def test_reimportar_nao_recria_parcela_excluida(self):
        Parcela.objects.filter(nosso_numero=self.PATRICIA).delete()
        stats = importar_titulos(ler_extrato(EXTRATO), HOJE)
        self.assertFalse(Parcela.objects.filter(nosso_numero=self.PATRICIA).exists())
        self.assertEqual(stats["titulos_excluidos_ignorados"], 1)
        self.assertEqual(Parcela.objects.count(), 6)

    def test_remover_da_lista_de_excluidos_libera_o_titulo(self):
        from financeiro.models import TituloExcluido

        Parcela.objects.filter(nosso_numero=self.PATRICIA).delete()
        TituloExcluido.objects.filter(nosso_numero=self.PATRICIA).delete()
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        self.assertTrue(Parcela.objects.filter(nosso_numero=self.PATRICIA).exists())

    def test_excluir_pelo_admin(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        ids = list(Parcela.objects.a_verificar().values_list("pk", flat=True))
        self.assertEqual(len(ids), 1)
        resp = self.client.post(
            reverse("admin:financeiro_parcela_changelist"),
            {"action": "delete_selected", "_selected_action": ids, "post": "yes"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Parcela.objects.a_verificar().exists())
        resp = self.enviar_extrato()
        self.assertContains(resp, "1 título(s) excluído(s) anteriormente foram ignorados")

    def enviar_extrato(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        arquivo = SimpleUploadedFile("extrato.txt", EXTRATO.encode("latin-1"), content_type="text/plain")
        return self.client.post(reverse("financeiro:importar_extrato"), {"arquivo": arquivo}, follow=True)


class GruposDeAcessoTests(TestCase):
    def criar_funcionario(self, grupo):
        from django.contrib.auth.models import Group

        u = User.objects.create_user("func", password="senha-forte-123", is_staff=True)
        u.groups.add(Group.objects.get(name=grupo))
        self.client.login(username="func", password="senha-forte-123")
        return u

    def test_grupo_financeiro_altera_e_importa_mas_nao_exclui(self):
        u = self.criar_funcionario("Financeiro")
        self.assertTrue(u.has_perm("financeiro.change_parcela"))
        self.assertFalse(u.has_perm("financeiro.delete_parcela"))
        self.assertEqual(self.client.get(reverse("financeiro:importar_extrato")).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:painel")).status_code, 200)

    def test_grupo_consulta_so_visualiza(self):
        u = self.criar_funcionario("Consulta")
        self.assertTrue(u.has_perm("financeiro.view_parcela"))
        self.assertFalse(u.has_perm("financeiro.change_parcela"))
        self.assertEqual(self.client.get(reverse("financeiro:painel")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:financeiro_parcela_changelist")).status_code, 200)
        self.assertRedirects(
            self.client.get(reverse("financeiro:importar_extrato")), reverse("financeiro:painel")
        )


class TemaClaroEscuroTests(TestCase):
    def test_botao_de_tema_no_login_no_painel_e_na_importacao(self):
        self.assertContains(self.client.get(reverse("admin:login")), 'class="theme-toggle"')
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        for url in [reverse("financeiro:painel"), reverse("financeiro:importar_extrato"), reverse("admin:index")]:
            resp = self.client.get(url)
            self.assertContains(resp, 'class="theme-toggle"', count=1, msg_prefix=url)
            self.assertContains(resp, "financeiro/tema.css", msg_prefix=url)

    def test_selo_usa_classes_do_tema(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        resp = self.client.get(reverse("admin:financeiro_parcela_changelist"))
        self.assertContains(resp, 'class="selo selo-VENCIDO"')
