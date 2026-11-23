from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from financeiro import boleto as b
from financeiro.models import Cliente, ConvenioCaixa, Financiamento, Parcela

HOJE = date(2026, 10, 28)


class CalculosTests(TestCase):
    def test_carne_de_exemplo(self):
        # Carne de 3 parcelas de R$ 500,00 no convenio de exemplo (beneficiario 7654321).
        carne = [
            ("14000000000020001", date(2026, 4, 30), "10497.65439 21000.100046 00002.000123 2 14320000050000", 3),
            ("14000000000020002", date(2026, 5, 30), "10497.65439 21000.100046 00002.000206 7 14620000050000", 1),
            ("14000000000020003", date(2026, 6, 30), "10497.65439 21000.100046 00002.000396 7 14930000050000", 0),
        ]
        for nosso_numero, vencimento, linha, dv in carne:
            codigo = b.codigo_de_barras("7654321", nosso_numero, vencimento, Decimal("500.00"))
            self.assertEqual(len(codigo), 44)
            self.assertEqual(b.linha_digitavel(codigo), linha)
            self.assertEqual(b.dv_nosso_numero(nosso_numero), dv)

    def test_exemplos_do_manual(self):
        self.assertEqual(b.dv_nosso_numero("14000000000000019"), 7)  # Anexo IV
        self.assertEqual(b.dv_modulo10("104900550"), 5)  # Anexo V
        self.assertEqual(b.dv_codigo_beneficiario("005507"), 7)  # Anexo VI
        self.assertEqual(b.dv_campo_livre("005507722213334777777777"), 1)  # Anexo III
        # Anexo I: codigo do exemplo com o DV geral (posicao 5) retirado.
        codigo = "1049" + "3242" + "0000032112" + "0055077222133347777777771"
        self.assertEqual(b.dv_geral(codigo), 4)
        completo = codigo[:4] + "4" + codigo[4:]
        self.assertEqual(b.linha_digitavel(completo), "10490.05505 77222.133348 77777.777713 4 32420000032112")

    def test_fator_de_vencimento_reiniciado_em_2025(self):
        self.assertEqual(b.fator_vencimento(date(2000, 7, 3)), 1000)
        self.assertEqual(b.fator_vencimento(date(2025, 2, 21)), 9999)
        self.assertEqual(b.fator_vencimento(date(2025, 2, 22)), 1000)
        self.assertEqual(b.fator_vencimento(date(2026, 12, 21)), 1667)
        self.assertEqual(b.fator_vencimento(date(2049, 10, 13)), 9999)

    def test_dv_geral_nunca_e_zero(self):
        for i in range(300):
            codigo = b.codigo_de_barras("7654321", f"14{i:015d}", date(2026, 11, 1), Decimal("100.00") + i)
            self.assertNotEqual(codigo[4], "0")

    def test_barras_2_de_5_intercalado(self):
        barras = b.barras_2de5_intercalado("0" * 44)
        self.assertEqual(barras[:4], [(True, 1), (False, 1), (True, 1), (False, 1)])
        self.assertEqual(barras[-3:], [(True, 3), (False, 1), (True, 1)])
        self.assertEqual(sum(largura for _, largura in barras), 405)  # 44 digitos, larga = 3 estreitas

    def test_recusa_dados_fora_do_padrao(self):
        with self.assertRaises(b.BoletoInvalido):
            b.codigo_de_barras("765432", "14000000000020001", date(2026, 4, 30), Decimal("500"))
        with self.assertRaises(b.BoletoInvalido):
            b.codigo_de_barras("7654321", "24000000000020001", date(2026, 4, 30), Decimal("500"))
        with self.assertRaises(b.BoletoInvalido):
            b.codigo_de_barras("7654321", "14000000000020001", date(2026, 4, 30), Decimal("10000000"))


class EmissaoTests(TestCase):
    def setUp(self):
        self.conv = ConvenioCaixa.objects.get()
        self.conv.dv_agencia, self.conv.cpf_cnpj, self.conv.nome = "0", "11222333000181", "CONSTRUTORA EXEMPLO LTDA"
        self.conv.endereco, self.conv.bairro, self.conv.cidade, self.conv.uf, self.conv.cep = (
            "Rua Exemplo, 100", "Centro", "Campinas", "sp", "13000000"
        )
        self.conv.juros_codigo, self.conv.juros_valor = "2", Decimal("5.00")
        self.conv.multa_codigo, self.conv.multa_valor = "1", Decimal("10.00")
        self.conv.save()
        self.cliente = Cliente.objects.create(
            nome="LUCAS HENRIQUE MOREIRA", cpf_cnpj="52998224725", endereco="Rua das Flores, 24",
            bairro="Jardim Exemplo", cidade="Campinas", uf="SP", cep="13036151",
        )
        self.fin = Financiamento.objects.create(
            cliente=self.cliente, numero_documento="001", total_parcelas=200, data_entrada=date(2026, 3, 10)
        )

    def parcela(self, i, **campos):
        dados = dict(
            financiamento=self.fin, numero=i, nosso_numero=f"14{i + 20000:015d}", valor=Decimal("500.00") + i,
            vencimento=HOJE + timedelta(days=i), situacao_banco="EM ABERTO",
        )
        dados.update(campos)
        return Parcela.objects.create(**dados)

    def test_dados_impressos(self):
        boleto = b.boleto_da_parcela(self.parcela(1), self.conv, HOJE)
        self.assertEqual(boleto.agencia_codigo, "0123 / 7654321")
        self.assertEqual(boleto.nosso_numero, "14000000000020001-3")
        self.assertEqual(boleto.documento, "001 /001/200")
        self.assertEqual(boleto.especie, "DM")
        self.assertEqual(boleto.beneficiario_endereco, "Rua Exemplo, 100, Centro, Campinas - SP - CEP 13000-000")
        self.assertEqual(boleto.instrucoes, [
            "JUROS DE 5,00% AO MÊS (DIAS CORRIDOS) A PARTIR DE 30/10/2026",
            "MULTA DE R$ 10,00 A PARTIR DE 30/10/2026",
            "NÃO RECEBER APÓS 30 DIAS DE ATRASO",
        ])

    def test_so_boleto_em_aberto_com_cpf(self):
        casos = [
            self.parcela(1, data_pagamento=HOJE),
            self.parcela(2, situacao_banco="LIQUIDADO"),
            self.parcela(3, nosso_numero=None),
        ]
        for p in casos:
            with self.assertRaises(b.BoletoInvalido):
                b.boleto_da_parcela(p, self.conv, HOJE)
        self.cliente.cpf_cnpj = ""
        self.cliente.save()
        with self.assertRaisesMessage(b.BoletoInvalido, "CPF"):
            b.boleto_da_parcela(self.parcela(4), self.conv, HOJE)

    def test_massa_de_teste_cobre_todos_os_dvs(self):
        for i in range(1, 150):
            self.parcela(i)
        boletos, faltando = b.massa_de_teste(Parcela.objects.order_by("vencimento"), self.conv, HOJE)
        self.assertEqual(faltando, [])
        self.assertTrue(10 <= len(boletos) <= 20)
        self.assertEqual({x.dv_geral for x in boletos}, set(range(1, 10)))
        self.assertEqual({x.dv_campo_livre for x in boletos}, set(range(10)))

    def test_massa_de_teste_avisa_o_que_falta(self):
        self.parcela(1)
        boletos, faltando = b.massa_de_teste(Parcela.objects.all(), self.conv, HOJE)
        self.assertEqual(len(boletos), 1)
        self.assertEqual(len(faltando), 17)

    def test_pdf(self):
        boletos = [b.boleto_da_parcela(self.parcela(i), self.conv, HOJE) for i in (1, 2)]
        leitor = PdfReader(BytesIO(b.gerar_pdf(boletos)))
        self.assertEqual(len(leitor.pages), 2)
        texto = leitor.pages[0].extract_text()
        for trecho in [boletos[0].linha_digitavel, "14000000000020001-3", "0123 / 7654321", "Recibo do Pagador",
                       "Ficha de Compensação", "SAC CAIXA", "LUCAS HENRIQUE MOREIRA", "529.982.247-25"]:
            self.assertIn(trecho, texto)

    def test_admin(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        parcelas = [self.parcela(i) for i in range(1, 150)]
        resp = self.client.post(
            reverse("admin:financeiro_parcela_changelist"),
            {"action": "gerar_boletos", "_selected_action": [parcelas[0].pk]},
        )
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("boleto_14000000000020001", resp["Content-Disposition"])

        resp = self.client.get(reverse("admin:financeiro_conveniocaixa_change", args=[self.conv.pk]))
        self.assertContains(resp, reverse("admin:financeiro_conveniocaixa_massa"))
        resp = self.client.get(reverse("admin:financeiro_conveniocaixa_massa"))
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("massa_de_teste_caixa.pdf", resp["Content-Disposition"])

        self.conv.cep = ""
        self.conv.save()
        resp = self.client.get(reverse("admin:financeiro_conveniocaixa_massa"), follow=True)
        self.assertContains(resp, "Preencha no convênio Caixa: CEP.")

    def test_admin_sugere_clientes_para_completar(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        self.parcela(1)
        sem_cpf = Cliente.objects.create(nome="FULANO SEM CPF")
        fin = Financiamento.objects.create(cliente=sem_cpf, numero_documento="002", total_parcelas=200)
        for i in range(2, 150):
            self.parcela(i, financiamento=fin)
        resp = self.client.get(reverse("admin:financeiro_conveniocaixa_massa"), follow=True)
        self.assertContains(resp, "Preencha o CPF/CNPJ destes clientes e tente de novo: FULANO SEM CPF.")
