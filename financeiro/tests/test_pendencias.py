from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from financeiro.extrato import ler_extrato
from financeiro.importacao import importar_titulos
from financeiro.models import Cliente, Parcela, PendenciaCaixa
from financeiro.tests.test_importacao import EXTRATO, HOJE
from financeiro.validadores import formatar_cpf_cnpj, validar_cpf_cnpj

ABERTA = "14000000000020011"  # LUCAS, parcela 3/14, EM ABERTO, venc. 20/10/2026, R$ 1.180,45
PAGA = "14000000000020010"  # LUCAS, parcela 2/14, LIQUIDADO
BAIXADA = "14000000000050015"  # PATRICIA, BAIXADO POR DEVOLUCAO

P = PendenciaCaixa


def pendentes(**filtro):
    return PendenciaCaixa.objects.filter(status=P.Status.PENDENTE, **filtro)


class ValidadoresTests(TestCase):
    def test_cpf_e_cnpj(self):
        validar_cpf_cnpj("529.982.247-25")
        validar_cpf_cnpj("11.222.333/0001-81")
        for invalido in ["529.982.247-24", "111.111.111-11", "123", "11.222.333/0001-80"]:
            with self.assertRaises(ValidationError, msg=invalido):
                validar_cpf_cnpj(invalido)

    def test_formatacao_ao_salvar(self):
        c = Cliente.objects.create(nome="X", cpf_cnpj="52998224725", cep="13000000", uf="sp")
        self.assertEqual((c.cpf_cnpj, c.cep, c.uf), ("529.982.247-25", "13000-000", "SP"))
        self.assertEqual(formatar_cpf_cnpj("11222333000181"), "11.222.333/0001-81")


class PendenciasParcelaTests(TestCase):
    def setUp(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)

    def parcela(self, nn):
        return Parcela.objects.get(nosso_numero=nn)

    def test_importar_nao_gera_pendencia(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        self.assertFalse(PendenciaCaixa.objects.exists())

    def test_alterar_vencimento_e_valor(self):
        p = self.parcela(ABERTA)
        p.vencimento, p.valor = date(2026, 10, 25), Decimal("1300.00")
        p.save()
        venc = pendentes(tipo=P.Tipo.ALTERAR_VENCIMENTO).get()
        self.assertEqual((venc.antes, venc.depois), ({"vencimento": "2026-10-20"}, {"vencimento": "2026-10-25"}))
        self.assertEqual(venc.cliente, "LUCAS HENRIQUE MOREIRA")
        self.assertEqual(pendentes(tipo=P.Tipo.ALTERAR_VALOR).get().depois, {"valor": "1300.00"})

    def test_alterar_duas_vezes_atualiza_e_desfazer_cancela(self):
        p = self.parcela(ABERTA)
        p.vencimento = date(2026, 10, 25)
        p.save()
        p.vencimento = date(2026, 10, 28)
        p.save()
        pend = pendentes().get()
        self.assertEqual(pend.depois, {"vencimento": "2026-10-28"})
        self.assertIn("venc. 28/10/2026", pend.descricao_parcela)
        self.assertEqual(pend.titulo["vencimento"], "2026-10-28")
        p.vencimento = date(2026, 10, 20)
        p.save()
        self.assertFalse(pendentes().exists())
        self.assertEqual(PendenciaCaixa.objects.get().status, P.Status.CANCELADA)

    def test_parcela_paga_ou_baixada_no_banco_nao_gera_pendencia(self):
        for nn in (PAGA, BAIXADA):
            p = self.parcela(nn)
            p.vencimento = date(2027, 1, 1)
            p.save()
        self.assertFalse(PendenciaCaixa.objects.exists())

    def test_marcar_paga_no_sistema_pede_baixa_e_cancela_alteracoes(self):
        p = self.parcela(ABERTA)
        p.vencimento = date(2026, 10, 25)
        p.save()
        p.data_pagamento = date(2026, 9, 30)
        p.save()
        self.assertEqual(pendentes().get().tipo, P.Tipo.BAIXA)
        self.assertIn("paga fora do boleto", pendentes().get().motivo)
        # desmarcar o pagamento cancela o pedido de baixa
        p.data_pagamento = None
        p.save()
        self.assertFalse(pendentes(tipo=P.Tipo.BAIXA).exists())

    def test_excluir_parcela_pede_baixa(self):
        self.parcela(ABERTA).delete()
        baixa = pendentes().get()
        self.assertEqual((baixa.tipo, baixa.nosso_numero, baixa.parcela), (P.Tipo.BAIXA, ABERTA, None))
        self.assertIn("3/14", baixa.descricao_parcela)

    def test_excluir_parcela_ja_baixada_no_banco_nao_pede_baixa(self):
        self.parcela(BAIXADA).delete()
        self.assertFalse(PendenciaCaixa.objects.exists())

    def test_extrato_nao_desfaz_alteracao_pendente(self):
        p = self.parcela(ABERTA)
        p.vencimento = date(2026, 10, 25)
        p.save()
        importar_titulos(ler_extrato(EXTRATO), HOJE)  # extrato ainda com 20/10
        self.assertEqual(self.parcela(ABERTA).vencimento, date(2026, 10, 25))
        self.assertTrue(pendentes().exists())

    def test_extrato_confirma_alteracao_feita_no_banco(self):
        p = self.parcela(ABERTA)
        p.vencimento = date(2026, 10, 25)
        p.save()
        novo = EXTRATO.replace("1.180,45    20.10.2026    EM ABERTO", "1.180,45    25.10.2026    EM ABERTO")
        stats = importar_titulos(ler_extrato(novo), HOJE)
        self.assertEqual(stats["pendencias_resolvidas"], 1)
        self.assertEqual(PendenciaCaixa.objects.get().status, P.Status.CONFIRMADA)

    def test_extrato_confirma_baixa_de_parcela_excluida(self):
        self.parcela(ABERTA).delete()
        novo = EXTRATO.replace(
            "20.10.2026    EM ABERTO                                21.07.2025",
            "20.10.2026    BAIXADO POR DEVOLUCAO                    01.10.2026",
        )
        importar_titulos(ler_extrato(novo), HOJE)
        self.assertEqual(PendenciaCaixa.objects.get().status, P.Status.CONFIRMADA)
        self.assertFalse(Parcela.objects.filter(nosso_numero=ABERTA).exists())


class PendenciasClienteTests(TestCase):
    def setUp(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        self.cliente = Cliente.objects.get(nome="LUCAS HENRIQUE MOREIRA")

    def completar(self):
        c = self.cliente
        c.cpf_cnpj, c.endereco, c.bairro, c.cidade, c.uf, c.cep = (
            "529.982.247-25", "Rua A, 10", "Centro", "Campinas", "SP", "13000-000"
        )
        c.save()

    def test_completar_cadastro_nao_gera_pendencia(self):
        self.completar()
        self.assertTrue(self.cliente.cadastro_completo)
        self.assertFalse(PendenciaCaixa.objects.exists())

    def test_alterar_cliente_completo_gera_pendencia_por_boleto_em_aberto(self):
        self.completar()
        self.cliente.cep = "13010-000"
        self.cliente.save()
        pend = pendentes(tipo=P.Tipo.ALTERAR_PAGADOR).get()  # so a 3/14 esta em aberto no banco
        self.assertEqual(pend.nosso_numero, ABERTA)
        self.assertEqual((pend.antes["cep"], pend.depois["cep"]), ("13000-000", "13010-000"))
        self.cliente.endereco = "Rua B, 20"
        self.cliente.save()
        self.assertEqual(pendentes().count(), 1)  # atualiza a mesma pendencia


class PendenciasTelasTests(TestCase):
    def setUp(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        self.func = User.objects.create_user("func", password="senha-forte-123", is_staff=True)
        self.func.groups.add(Group.objects.get(name="Financeiro"))
        self.client.login(username="func", password="senha-forte-123")

    def alterar_vencimento_pelo_admin(self):
        p = Parcela.objects.get(nosso_numero=ABERTA)
        url = reverse("admin:financeiro_parcela_change", args=[p.pk])
        dados = {
            "financiamento": p.financiamento_id, "numero": p.numero, "nosso_numero": p.nosso_numero,
            "valor": "1180.45", "vencimento": "25/10/2026", "data_pagamento": "", "observacoes": "",
        }
        resp = self.client.post(url, dados)
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", {}) and resp.context["adminform"].form.errors)

    def test_quem_alterou_fica_na_pendencia_e_aparece_no_painel(self):
        self.alterar_vencimento_pelo_admin()
        self.assertEqual(pendentes().get().criado_por, self.func)
        self.assertContains(self.client.get(reverse("financeiro:painel")), "1 alteração(ões) pendente(s) para a Caixa")
        resp = self.client.get(reverse("admin:financeiro_pendenciacaixa_changelist"))
        self.assertContains(resp, "20/10/2026 → <strong>25/10/2026</strong>")

    def test_marcar_enviada(self):
        self.alterar_vencimento_pelo_admin()
        pend = pendentes().get()
        self.client.post(
            reverse("admin:financeiro_pendenciacaixa_changelist"),
            {"action": "marcar_enviada", "_selected_action": [pend.pk]},
        )
        pend.refresh_from_db()
        self.assertEqual((pend.status, pend.enviado_por), (P.Status.ENVIADA, self.func))

    def test_acao_marcar_pago_pede_baixa(self):
        p = Parcela.objects.get(nosso_numero=ABERTA)
        self.client.post(
            reverse("admin:financeiro_parcela_changelist"),
            {"action": "marcar_pago_hoje", "_selected_action": [p.pk]},
        )
        self.assertEqual(pendentes().get().tipo, P.Tipo.BAIXA)

    def test_cadastro_de_cliente_valida_cpf(self):
        c = Cliente.objects.get(nome="LUCAS HENRIQUE MOREIRA")
        resp = self.client.post(
            reverse("admin:financeiro_cliente_change", args=[c.pk]),
            {
                "nome": c.nome, "cpf_cnpj": "111.111.111-11", "endereco": "", "bairro": "", "cidade": "",
                "uf": "", "cep": "", "telefone": "", "email": "", "observacoes": "",
                "financiamentos-TOTAL_FORMS": 0, "financiamentos-INITIAL_FORMS": 0,
            },
        )
        self.assertContains(resp, "CPF ou CNPJ inválido")
