from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from financeiro.extrato import ler_extrato
from financeiro.importacao import importar_titulos
from financeiro.models import Cliente, ConvenioCaixa, Parcela, PendenciaCaixa, RemessaCaixa
from financeiro.remessa import ConvenioIncompleto, gerar_remessa, registro
from financeiro.tests.test_importacao import EXTRATO, HOJE

ABERTA = "14000000000020011"  # LUCAS, parcela 3/14, EM ABERTO, venc. 20/10/2026, R$ 1.180,45
OUTRA = "14000000000040013"  # MARIA CLARA, 13/13, EM ABERTO, venc. 30/08/2026, R$ 1.734,20
AGORA = datetime(2026, 10, 1, 14, 30, 5, tzinfo=ZoneInfo("America/Sao_Paulo"))
P = PendenciaCaixa


def campo(linha, inicio, fim):
    """Posicoes como no manual (comecam em 1, fim incluido)."""
    return linha[inicio - 1 : fim]


class RemessaTests(TestCase):
    def setUp(self):
        importar_titulos(ler_extrato(EXTRATO), HOJE)
        self.conv = ConvenioCaixa.objects.get()
        self.conv.dv_agencia, self.conv.cpf_cnpj, self.conv.nome = "0", "11222333000181", "Construtora Exemplo Ltda"
        self.conv.save()
        Cliente.objects.filter(nome__startswith="LUCAS").update(
            cpf_cnpj="529.982.247-25", endereco="Rua São João, 10", bairro="Centro",
            cidade="Campinas", uf="SP", cep="13000-123",
        )

    def parcela(self, nn):
        return Parcela.objects.get(nosso_numero=nn)

    def alterar(self, nn, **campos):
        p = self.parcela(nn)
        for k, v in campos.items():
            setattr(p, k, v)
        p.save()

    def gerar(self, **filtro):
        return gerar_remessa(P.objects.filter(**filtro), agora=AGORA)

    def test_convenio_inicial(self):
        self.assertEqual((self.conv.agencia, self.conv.codigo_beneficiario), ("0123", "7654321"))
        self.assertTrue(self.conv.ambiente_teste)

    def test_registro_confere_posicoes(self):
        with self.assertRaises(ValueError):
            registro((1, 3, "9", "104"), (5, 240, "X", ""))  # pulou a posicao 4
        with self.assertRaises(ValueError):
            registro((1, 3, "9", "1040"), (4, 240, "X", ""))  # nao cabe

    def test_arquivo_de_alteracao_de_vencimento_e_valor(self):
        self.alterar(ABERTA, vencimento=date(2026, 10, 25), valor=Decimal("1300.00"))
        remessa, ignoradas = self.gerar()
        self.assertEqual(ignoradas, [])
        self.assertTrue(remessa.conteudo.endswith("\r\n"))
        linhas = remessa.conteudo.split("\r\n")[:-1]
        self.assertEqual([len(linha) for linha in linhas], [240] * 8)
        self.assertEqual([linha[7] for linha in linhas], list("01333359"))
        self.assertEqual([linha[13] for linha in linhas[2:6]], list("PQPQ"))

        header, lote, p1, q1, p2, _, trailer_lote, trailer = linhas
        self.assertEqual(campo(header, 1, 3), "104")
        self.assertEqual(campo(header, 18, 32), "211222333000181")
        self.assertEqual(campo(header, 53, 65), "001230" + "7654321")
        self.assertEqual(campo(header, 73, 102).strip(), "CONSTRUTORA EXEMPLO LTDA")
        self.assertEqual(campo(header, 143, 166), "1" + "01102026" + "143005" + "000001" + "107")
        self.assertEqual(campo(header, 192, 211).strip(), "REMESSA-TESTE")

        self.assertEqual(campo(lote, 9, 16), "R0100067")
        self.assertEqual(campo(lote, 34, 40), "7654321")
        self.assertEqual(campo(lote, 54, 65), "001230" + "000000")
        self.assertEqual(campo(lote, 184, 199), "00000001" + "01102026")

        movimentos = {campo(p1, 16, 17), campo(p2, 16, 17)}
        self.assertEqual(movimentos, {"06", "47"})
        self.assertEqual(campo(p1, 9, 13), "00001")
        self.assertEqual(campo(p1, 24, 30), "7654321")
        self.assertEqual(campo(p1, 41, 57), ABERTA)
        self.assertEqual(campo(p1, 58, 62), "11220")
        self.assertEqual(campo(p1, 63, 73).strip(), "001")
        self.assertEqual(campo(p1, 78, 100), "25102026" + "000000000130000")
        self.assertEqual(campo(p1, 110, 118), "21072025" + "3")
        self.assertEqual(campo(p1, 228, 229), "09")

        self.assertEqual(campo(q1, 9, 17), "00002Q 06" if campo(p1, 16, 17) == "06" else "00002Q 47")
        self.assertEqual(campo(q1, 18, 33), "1" + "000052998224725")
        self.assertEqual(campo(q1, 34, 73).strip(), "LUCAS HENRIQUE MOREIRA")
        self.assertEqual(campo(q1, 74, 113).strip(), "RUA SAO JOAO 10")
        self.assertEqual(campo(q1, 129, 153), "13000123" + "CAMPINAS".ljust(15) + "SP")

        self.assertEqual(campo(trailer_lote, 18, 46), "000006" + "000002" + "00000000000260000")
        self.assertEqual(campo(trailer, 18, 29), "000001" + "000008")

    def test_em_teste_pendencias_continuam_pendentes_e_nsa_avanca(self):
        self.alterar(ABERTA, vencimento=date(2026, 10, 25))
        primeira, _ = self.gerar()
        segunda, _ = self.gerar()
        self.assertEqual((primeira.nsa, segunda.nsa), (1, 2))
        self.assertTrue(segunda.teste)
        pend = P.objects.get()
        self.assertEqual((pend.status, pend.remessa), (P.Status.PENDENTE, segunda))

    def test_em_producao_marca_enviada_e_extrato_nao_desfaz_ate_confirmar(self):
        self.conv.ambiente_teste = False
        self.conv.save()
        self.alterar(ABERTA, vencimento=date(2026, 10, 25))
        remessa, _ = self.gerar()
        self.assertEqual(campo(remessa.conteudo, 192, 211).strip(), "")
        self.assertEqual(P.objects.get().status, P.Status.ENVIADA)

        importar_titulos(ler_extrato(EXTRATO), HOJE)  # banco ainda nao processou
        self.assertEqual(self.parcela(ABERTA).vencimento, date(2026, 10, 25))
        self.assertEqual(P.objects.get().status, P.Status.ENVIADA)

        importar_titulos(ler_extrato(EXTRATO.replace("20.10.2026    EM ABERTO", "25.10.2026    EM ABERTO")), HOJE)
        self.assertEqual(P.objects.get().status, P.Status.CONFIRMADA)

    def test_cliente_sem_cpf_fica_fora(self):
        self.alterar(OUTRA, vencimento=date(2026, 11, 10))
        self.alterar(ABERTA, vencimento=date(2026, 10, 25))
        remessa, ignoradas = self.gerar()
        self.assertEqual(remessa.quantidade, 1)
        self.assertEqual([(p.nosso_numero, "CPF" in motivo) for p, motivo in ignoradas], [(OUTRA, True)])
        self.assertIsNone(P.objects.get(nosso_numero=OUTRA).remessa)

    def test_novo_vencimento_que_ja_passou_fica_fora(self):
        self.alterar(ABERTA, vencimento=date(2026, 9, 17))  # retorno da Caixa: 26 / motivo 17
        remessa, ignoradas = self.gerar()
        self.assertIsNone(remessa)
        self.assertIn("já passou", ignoradas[0][1])
        self.assertEqual(P.objects.get().status, P.Status.PENDENTE)
        self.alterar(ABERTA, vencimento=date(2026, 10, 1))  # hoje ainda vale
        remessa, ignoradas = self.gerar()
        self.assertEqual((remessa.quantidade, ignoradas), (1, []))

    def test_alterar_cpf_do_pagador_nao_vai_por_remessa(self):
        cliente = Cliente.objects.get(nome__startswith="LUCAS")
        cliente.cidade = "Arapiraca"
        cliente.save()
        cliente.cpf_cnpj = "111.444.777-35"
        cliente.save()
        pend = P.objects.get(tipo=P.Tipo.ALTERAR_PAGADOR)
        remessa, ignoradas = self.gerar()
        self.assertIsNone(remessa)
        self.assertEqual(ignoradas[0][0], pend)
        self.assertIn("baixe o boleto", ignoradas[0][1])

    def test_alterar_endereco_do_pagador_vai_com_movimento_31(self):
        cliente = Cliente.objects.get(nome__startswith="LUCAS")
        cliente.cidade = "Arapiraca"
        cliente.save()
        remessa, _ = self.gerar()
        p, q = remessa.conteudo.split("\r\n")[2:4]
        self.assertEqual((campo(p, 16, 17), campo(p, 61, 61)), ("31", "5"))
        self.assertEqual(campo(q, 137, 151).strip(), "ARAPIRACA")

    def test_multa_vai_no_segmento_r(self):
        self.conv.juros_codigo, self.conv.juros_valor = "2", Decimal("5.00")
        self.conv.multa_codigo, self.conv.multa_valor = "1", Decimal("10.00")
        self.conv.save()
        self.alterar(ABERTA, vencimento=date(2026, 10, 31))
        Cliente.objects.filter(nome__startswith="MARIA").update(cpf_cnpj="111.444.777-35")
        self.parcela(OUTRA).delete()  # baixa: sem segmento R
        remessa, _ = self.gerar()
        linhas = remessa.conteudo.split("\r\n")[:-1]
        self.assertEqual([len(linha) for linha in linhas], [240] * len(linhas))
        detalhes = linhas[2:-2]
        self.assertEqual(sorted(campo(d, 16, 17) + campo(d, 14, 14) for d in detalhes),
                         ["02P", "02Q", "06P", "06Q", "06R"])
        self.assertEqual([campo(d, 9, 13) for d in detalhes], ["00001", "00002", "00003", "00004", "00005"])
        i = [campo(d, 14, 17) for d in detalhes].index("P 06")
        p, r = detalhes[i], detalhes[i + 2]
        self.assertEqual(campo(p, 118, 141), "2" + "00000000" + "000000000000500")
        self.assertEqual(campo(r, 18, 65), "0" * 48)
        self.assertEqual(campo(r, 66, 89), "1" + "01112026" + "000000000001000")
        self.assertEqual(campo(r, 90, 240).strip(), "")
        self.assertEqual(campo(linhas[-2], 18, 29), "000007" + "000002")
        self.assertEqual(campo(linhas[-1], 24, 29), "000009")

    def test_multa_sem_valor_bloqueia(self):
        self.conv.multa_codigo = "2"
        self.conv.save()
        self.alterar(ABERTA, vencimento=date(2026, 10, 25))
        with self.assertRaisesMessage(ConvenioIncompleto, "valor da multa"):
            self.gerar()

    def test_baixa_de_parcela_excluida_usa_dados_guardados(self):
        self.parcela(ABERTA).delete()
        remessa, ignoradas = self.gerar()
        self.assertEqual(ignoradas, [])
        p, q = remessa.conteudo.split("\r\n")[2:4]
        self.assertEqual(campo(p, 16, 17), "02")
        self.assertEqual(campo(p, 41, 100), ABERTA + "11220" + "001".ljust(11) + "    " + "20102026" + "000000000118045")
        self.assertEqual(campo(q, 19, 33), "000052998224725")

    def test_convenio_incompleto(self):
        self.conv.dv_agencia = ""
        self.conv.save()
        self.alterar(ABERTA, vencimento=date(2026, 10, 25))
        with self.assertRaisesMessage(ConvenioIncompleto, "dígito da agência"):
            self.gerar()
        self.assertFalse(RemessaCaixa.objects.exists())

    def test_acao_no_admin_baixa_o_arquivo(self):
        User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.login(username="admin", password="senha-forte-123")
        self.alterar(ABERTA, vencimento=date(2026, 10, 25))
        resp = self.client.post(
            reverse("admin:financeiro_pendenciacaixa_changelist"),
            {"action": "gerar_arquivo_remessa", "_selected_action": list(P.objects.values_list("pk", flat=True))},
        )
        self.assertEqual(resp["Content-Disposition"], 'attachment; filename="remessa_000001.rem"')
        self.assertEqual(resp.content.decode("latin-1"), RemessaCaixa.objects.get().conteudo)
        resp = self.client.get(reverse("admin:financeiro_remessacaixa_changelist"))
        self.assertContains(resp, "remessa_000001.rem")
        resp = self.client.get(reverse("admin:financeiro_remessacaixa_baixar", args=[RemessaCaixa.objects.get().pk]))
        self.assertEqual(resp.status_code, 200)
