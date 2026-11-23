from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from financeiro.contas_pdf import _dv_modulo10, data_do_fator, ler_conta
from financeiro.models import ContaEscritorio

HOJE = date(2026, 10, 14)
ADD = reverse("admin:financeiro_contaescritorio_add")
ENVIAR = reverse("admin:financeiro_contaescritorio_enviar_pdfs")


def linha_boleto(fator, centavos, banco="237", livre="2367020003391925600022650"):
    """Linha digitavel de boleto bancario (47 digitos) com os digitos dos campos corretos."""
    c1 = banco + "9" + livre[:5]
    c2, c3 = livre[5:15], livre[15:25]
    campos = [c + str(_dv_modulo10(c)) for c in (c1, c2, c3)]
    return f"{campos[0][:5]}.{campos[0][5:]} {campos[1][:5]}.{campos[1][5:]} {campos[2][:5]}.{campos[2][5:]} 9 {fator:04d}{centavos:010d}"


def linha_arrecadacao(segmento, centavos, empresa="0000", livre="0" * 25):
    codigo = f"8{segmento}6" + "0" + f"{centavos:011d}" + empresa + livre
    blocos = [codigo[i:i + 11] for i in range(0, 44, 11)]
    return " ".join(f"{b}-{_dv_modulo10(b)}" for b in blocos)


def pdf_com_texto(linhas):
    """PDF minimo de uma pagina com as linhas de texto (Helvetica)."""
    escapar = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    conteudo = "BT /F1 9 Tf 30 800 Td 11 TL " + " ".join(f"({escapar(l)}) Tj T*" for l in linhas) + " ET"
    objetos = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        f"<< /Length {len(conteudo.encode('latin-1'))} >>\nstream\n{conteudo}\nendstream",
    ]
    saida, posicoes = "%PDF-1.4\n", []
    for i, objeto in enumerate(objetos, 1):
        posicoes.append(len(saida.encode("latin-1")))
        saida += f"{i} 0 obj\n{objeto}\nendobj\n"
    inicio_xref = len(saida.encode("latin-1"))
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n" + "".join(f"{p:010d} 00000 n \n" for p in posicoes)
    saida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n"
    return saida.encode("latin-1")


FATURA_INTERNET = [
    "FATURA",
    "Fibra Rapida Telecomunicacoes S.A.",
    "Vencimento",
    "15/10/2026",
    "Valor",
    "89,99",
    "Pagador",
    "Fulano de Tal - CPF: 000.000.000-00",
    "Beneficiário",
    "Fibra Rapida Telecomunicacoes S.A. - CNPJ: 00.000.000/0001-00",
    linha_boleto(1600, 8999),
]


class LeituraTests(TestCase):
    def test_fator_de_vencimento_antes_e_depois_do_reinicio(self):
        self.assertEqual(data_do_fator(1600, HOJE), date(2026, 10, 15))  # reinicio: 1000 = 22/02/2025
        self.assertEqual(data_do_fator(9999, date(2025, 2, 1)), date(2025, 2, 21))
        self.assertIsNone(data_do_fator(0, HOJE))

    def test_boleto_bancario(self):
        dados = ler_conta("\n".join(FATURA_INTERNET), HOJE)
        self.assertEqual(dados["valor"], Decimal("89.99"))
        self.assertEqual(dados["vencimento"], date(2026, 10, 15))
        self.assertEqual(dados["fornecedor"], "Fibra Rapida Telecomunicacoes S.A.")
        self.assertEqual(dados["categoria"], "INTERNET")
        self.assertEqual(dados["descricao"], "Internet - Fibra")
        self.assertEqual(len(dados["linha_digitavel"]), 47)

    def test_linha_com_digito_errado_e_ignorada_e_usa_o_texto(self):
        linha = linha_boleto(1600, 12345)
        errada = linha[:10] + str((int(linha[10]) + 1) % 10) + linha[11:]  # troca o DV do 1o campo
        dados = ler_conta(f"Vencimento: 20/10/2026\nValor do documento R$ 1.234,56\n{errada}", HOJE)
        self.assertNotIn("linha_digitavel", dados)
        self.assertEqual((dados["valor"], dados["vencimento"]), (Decimal("1234.56"), date(2026, 10, 20)))

    def test_conta_de_consumo(self):
        texto = f"COMPANHIA X\nData de vencimento 25/10/2026\n{linha_arrecadacao('3', 15432)}"
        dados = ler_conta(texto, HOJE)
        self.assertEqual(dados["valor"], Decimal("154.32"))
        self.assertEqual(dados["vencimento"], date(2026, 10, 25))
        self.assertEqual(dados["categoria"], "ENERGIA")  # segmento 3

    def test_guia_da_prefeitura(self):
        texto = "\n".join([
            "PREFEITURA MUNICIPAL DE CIDADE NOVA",
            "SECRETARIA DA FAZENDA",
            "DAM - DOCUMENTO DE ARRECADAÇÃO MUNICIPAL",
            "PREFEITURA MUNICIPAL DE CIDADE NOVA 08/10/2026",
            "1019/1019 TAXA DE AVERBAÇÃO",
            "Contribuinte: EMPRESA EXEMPLO LTDA",
            linha_arrecadacao("1", 4788, empresa="1019", livre="2026102200000000000123456"),
            "Vencimento Total",
            "BANCO DO BRASIL OU LOTÉRICAS. 22/10/2026",
        ])
        dados = ler_conta(texto, HOJE)
        self.assertEqual(dados["valor"], Decimal("47.88"))
        self.assertEqual(dados["vencimento"], date(2026, 10, 22))  # AAAAMMDD do codigo, conferido no texto
        self.assertEqual(dados["fornecedor"], "Prefeitura Municipal de Cidade Nova")
        self.assertEqual(dados["categoria"], "IMPOSTOS")
        self.assertEqual(dados["descricao"], "Taxa de averbação - Prefeitura")

    def test_data_no_codigo_so_vale_se_estiver_escrita_na_guia(self):
        linha = linha_arrecadacao("1", 4788, empresa="1019", livre="2026102200000000000123456")
        self.assertNotIn("vencimento", ler_conta(f"PREFEITURA MUNICIPAL DE X\n{linha}", HOJE))

    def test_sem_nada_reconhecivel(self):
        self.assertEqual(ler_conta("texto qualquer", HOJE), {})


@mock.patch("financeiro.admin.timezone.localdate", return_value=HOJE)
class CadastroPorPdfTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("fin", password="senha-forte-123", is_staff=True)
        self.user.groups.add(Group.objects.get(name="Financeiro"))
        self.client.force_login(self.user)

    def pdf(self, linhas=FATURA_INTERNET, nome="fatura.pdf"):
        return SimpleUploadedFile(nome, pdf_com_texto(linhas), content_type="application/pdf")

    def test_adicionar_so_com_o_pdf_preenche_tudo_e_guarda(self, _):
        resposta = self.client.post(ADD, {"arquivo_pdf": self.pdf(), "categoria": "OUTROS"})
        self.assertEqual(resposta.status_code, 302, resposta.context and resposta.context["errors"])
        conta = ContaEscritorio.objects.get()
        self.assertEqual(
            (conta.descricao, conta.categoria, conta.valor, conta.vencimento, conta.pdf_nome),
            ("Internet - Fibra", "INTERNET", Decimal("89.99"), date(2026, 10, 15), "fatura.pdf"),
        )
        self.assertTrue(bytes(conta.pdf).startswith(b"%PDF"))
        # O PDF abre pelo sistema.
        resposta = self.client.get(reverse("admin:financeiro_contaescritorio_pdf", args=[conta.pk]))
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertTrue(resposta.content.startswith(b"%PDF"))
        self.assertContains(self.client.get(reverse("admin:financeiro_contaescritorio_changelist")), "abrir")

    def test_o_que_foi_digitado_tem_preferencia(self, _):
        self.client.post(ADD, {"arquivo_pdf": self.pdf(), "descricao": "Internet da sala", "valor": "100.00",
                               "categoria": "OUTROS"})
        conta = ContaEscritorio.objects.get()
        self.assertEqual((conta.descricao, conta.valor, conta.vencimento), ("Internet da sala", Decimal("100.00"), date(2026, 10, 15)))

    def test_pdf_sem_dados_pede_para_preencher(self, _):
        resposta = self.client.post(ADD, {"arquivo_pdf": self.pdf(["Documento escaneado"]), "categoria": "OUTROS"})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não encontrei no PDF")
        self.assertFalse(ContaEscritorio.objects.exists())

    def test_arquivo_que_nao_e_pdf(self, _):
        arquivo = SimpleUploadedFile("conta.pdf", b"nao sou pdf", content_type="application/pdf")
        resposta = self.client.post(ADD, {"arquivo_pdf": arquivo, "categoria": "OUTROS"})
        self.assertContains(resposta, "não é um PDF")

    def test_sem_pdf_continua_obrigatorio(self, _):
        resposta = self.client.post(ADD, {"categoria": "OUTROS"})
        self.assertContains(resposta, "Este campo é obrigatório.")

    def test_trocar_pdf_na_edicao(self, _):
        conta = ContaEscritorio.objects.create(descricao="Luz", valor=Decimal("10"), vencimento=HOJE)
        url = reverse("admin:financeiro_contaescritorio_change", args=[conta.pk])
        self.client.post(url, {"descricao": "Luz", "categoria": "ENERGIA", "valor": "10.00", "vencimento": "14/10/2026",
                               "arquivo_pdf": self.pdf(nome="luz.pdf")})
        conta.refresh_from_db()
        self.assertEqual((conta.pdf_nome, conta.valor, conta.fornecedor), ("luz.pdf", Decimal("10.00"), "Fibra Rapida Telecomunicacoes S.A."))

    def test_enviar_varios_pdfs(self, _):
        outra = [*FATURA_INTERNET[:-1], linha_boleto(1610, 5000)]
        resposta = self.client.post(ENVIAR, {"arquivos": [self.pdf(), self.pdf(outra, "nov.pdf"), self.pdf(["nada"], "x.pdf")]})
        self.assertEqual(resposta.status_code, 302)
        contas = list(ContaEscritorio.objects.order_by("vencimento"))
        self.assertEqual([(c.valor, c.vencimento) for c in contas], [(Decimal("89.99"), date(2026, 10, 15)), (Decimal("50.00"), date(2026, 10, 25))])
        mensagens = [str(m) for m in resposta.wsgi_request._messages]
        self.assertIn("2 conta(s) cadastrada(s) a partir dos PDFs. Confira os dados.", mensagens)
        self.assertTrue(any(m.startswith("x.pdf:") for m in mensagens))
        # Mandar de novo nao duplica.
        self.client.post(ENVIAR, {"arquivos": [self.pdf()]})
        self.assertEqual(ContaEscritorio.objects.count(), 2)

    def test_consulta_nao_envia_mas_ve_o_pdf(self, _):
        conta = ContaEscritorio.objects.create(descricao="Luz", valor=Decimal("10"), vencimento=HOJE,
                                               pdf=pdf_com_texto(["x"]), pdf_nome="luz.pdf")
        u = User.objects.create_user("cons", password="senha-forte-123", is_staff=True)
        u.groups.add(Group.objects.get(name="Consulta"))
        self.client.force_login(u)
        self.assertEqual(self.client.get(ENVIAR).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:financeiro_contaescritorio_pdf", args=[conta.pk])).status_code, 200)
