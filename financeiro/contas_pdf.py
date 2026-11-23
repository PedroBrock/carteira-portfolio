"""Leitura de faturas/boletos em PDF para preencher as contas do escritorio.

O que da para confiar vem da linha digitavel:
- boleto bancario (47 digitos): valor e vencimento (fator de vencimento);
- conta de consumo / arrecadacao (48 digitos, comeca com 8): valor e o segmento
  (energia, saneamento, telecomunicacoes...). O vencimento sai do texto.
O resto (vencimento sem linha digitavel, fornecedor) e procurado no texto da fatura.
PDF escaneado (so imagem) nao tem texto: nada e preenchido, mas o PDF e guardado.
"""

import io
import re
from datetime import date, timedelta
from decimal import Decimal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# 4 MB: a Vercel recusa envios e respostas acima de 4,5 MB. Uma fatura comum tem 30 a 300 KB.
TAMANHO_MAXIMO = 4 * 1024 * 1024


class PdfInvalido(Exception):
    pass


def texto_do_pdf(conteudo: bytes) -> str:
    if not conteudo.startswith(b"%PDF"):
        raise PdfInvalido("O arquivo não é um PDF.")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise PdfInvalido("O PDF passa de 4 MB.")
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)
    except (PdfReadError, ValueError, KeyError) as erro:
        raise PdfInvalido("Não foi possível abrir o PDF.") from erro


# ---------------------------------------------------------------- linha digitavel


def _dv_modulo10(numero: str) -> int:
    soma = 0
    for i, digito in enumerate(reversed(numero)):
        parcial = int(digito) * (2 if i % 2 == 0 else 1)
        soma += parcial // 10 + parcial % 10
    return (10 - soma % 10) % 10


def _dv_modulo11_arrecadacao(numero: str) -> int:
    soma = sum(int(d) * (2 + i % 8) for i, d in enumerate(reversed(numero)))
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def data_do_fator(fator: int, hoje: date) -> date | None:
    """Fator de vencimento -> data. O fator recomecou em 1000 em 22/02/2025; fica a data mais perto de hoje."""
    if fator == 0:
        return None
    candidatas = [date(1997, 10, 7) + timedelta(days=fator), date(2025, 2, 22) + timedelta(days=fator - 1000)]
    return min(candidatas, key=lambda d: abs((d - hoje).days))


RE_BOLETO = re.compile(r"(\d{5})\.?(\d{5})\s*(\d{5})\.?(\d{6})\s*(\d{5})\.?(\d{6})\s*(\d)\s*(\d{14})")
RE_ARRECADACAO = re.compile(r"(8\d{10})\s*-?\s*(\d)\s*(\d{11})\s*-?\s*(\d)\s*(\d{11})\s*-?\s*(\d)\s*(\d{11})\s*-?\s*(\d)")

# Segmento da conta de consumo (2o digito do codigo de arrecadacao).
SEGMENTO_CATEGORIA = {"2": "AGUA", "3": "ENERGIA", "4": "TELEFONE", "1": "IMPOSTOS", "5": "IMPOSTOS"}


def ler_boleto(texto: str, hoje: date) -> dict:
    for partes in RE_BOLETO.findall(texto):
        c1, c2, c3, c4, c5, c6, _dv_geral, fator_valor = partes
        campos = [(c1 + c2[:4], c2[4]), (c3 + c4[:5], c4[5]), (c5 + c6[:5], c6[5])]
        if all(_dv_modulo10(numero) == int(dv) for numero, dv in campos):
            linha = "".join(partes)
            dados = {"linha_digitavel": linha, "valor": Decimal(int(fator_valor[4:])) / 100}
            vencimento = data_do_fator(int(fator_valor[:4]), hoje)
            if vencimento:
                dados["vencimento"] = vencimento
            return dados
    return {}


def _vencimento_na_arrecadacao(codigo: str, texto: str) -> date | None:
    """Muitos orgaos (ex.: prefeituras) poem o vencimento AAAAMMDD no inicio do campo livre do codigo.

    Nao e obrigatorio, entao so vale se a mesma data tambem aparece escrita na fatura.
    """
    inicio = 23 if codigo[1] == "6" else 19  # segmento 6 identifica a empresa pelo CNPJ (8 digitos)
    bruto = codigo[inicio:inicio + 8]
    try:
        vencimento = date(int(bruto[:4]), int(bruto[4:6]), int(bruto[6:]))
    except ValueError:
        return None
    return vencimento if f"{vencimento:%d/%m/%Y}" in texto else None


def ler_arrecadacao(texto: str) -> dict:
    for partes in RE_ARRECADACAO.findall(texto):
        blocos = [(partes[i], partes[i + 1]) for i in range(0, 8, 2)]
        codigo = "".join(bloco for bloco, _ in blocos)
        dv_funcao = _dv_modulo10 if codigo[2] in "67" else _dv_modulo11_arrecadacao
        if not all(dv_funcao(bloco) == int(dv) for bloco, dv in blocos):
            continue
        dados = {"linha_digitavel": "".join(partes)}
        if codigo[2] in "67":  # valor efetivo em reais (8 e 9 = valor de referencia/quantidade)
            dados["valor"] = Decimal(int(codigo[4:15])) / 100
        if codigo[1] in SEGMENTO_CATEGORIA:
            dados["categoria"] = SEGMENTO_CATEGORIA[codigo[1]]
        if vencimento := _vencimento_na_arrecadacao(codigo, texto):
            dados["vencimento"] = vencimento
        return dados
    return {}


# ---------------------------------------------------------------- texto da fatura

RE_VENCIMENTO = re.compile(r"vencimento\W{0,20}?(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
RE_VALOR = re.compile(
    r"(?:valor do documento|valor a pagar|total a pagar|valor total|total da fatura|valor)\W{0,20}?(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
RE_BENEFICIARIO = re.compile(r"benefici[aá]rio[^\S\n]*\n?\s*([^\n]+?)\s*[-–]\s*(?:CNPJ|CPF)", re.IGNORECASE)
# Guias de orgao publico (DAM, DARF...) nao tem "Beneficiario": o orgao vem no cabecalho.
RE_ORGAO = re.compile(r"^\s*((?:PREFEITURA|MUNIC[IÍ]PIO|GOVERNO|ESTADO) [A-ZÀ-Ú ]+?)[^\S\n]*(?:\d{2}/\d{2}/\d{4})?$", re.MULTILINE)
RE_TRIBUTO = re.compile(r"\b(TAXA DE [A-ZÀ-Ú][A-ZÀ-Ú ]*|IPTU|ITBI|ISSQN|ISS)\b")

PALAVRAS_CATEGORIA = [
    ("ENERGIA", ["energia", "eletric", "neoenergia", "celpe", "coelba", "cosern", "enel", "cemig", "copel", "equatorial", "light s"]),
    ("AGUA", ["saneamento", "compesa", "sabesp", "embasa", "cagece", "copasa", "sanepar", "água e esgoto", "agua e esgoto"]),
    ("INTERNET", ["internet", "telecomunica", "brisanet", "fibra", "banda larga", "vivo", "claro", "oi s.a", "tim s.a"]),
    ("ALUGUEL", ["aluguel", "locação", "locacao", "imobili"]),
    ("CONDOMINIO", ["condomínio", "condominio"]),
    ("IMPOSTOS", ["prefeitura", "receita federal", "darf", "das ", "simples nacional", "iptu", "iss "]),
]


def _nome_proprio(texto: str) -> str:
    """'PREFEITURA MUNICIPAL DE CIDADE NOVA' -> 'Prefeitura Municipal de Cidade Nova'."""
    return " ".join(p.lower() if p.lower() in {"de", "da", "do", "das", "dos", "e"} else p.capitalize() for p in texto.split())


def _data(texto_data: str) -> date | None:
    dia, mes, ano = map(int, texto_data.split("/"))
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def adivinhar_categoria(*textos: str) -> str | None:
    for texto in textos:
        texto = (texto or "").lower()
        for categoria, palavras in PALAVRAS_CATEGORIA:
            if any(palavra in texto for palavra in palavras):
                return categoria
    return None


def ler_conta(texto: str, hoje: date) -> dict:
    """Dados encontrados na fatura: valor, vencimento, fornecedor, categoria, descricao, linha_digitavel."""
    dados = ler_boleto(texto, hoje) or ler_arrecadacao(texto)
    if "vencimento" not in dados and (achado := RE_VENCIMENTO.search(texto)):
        if vencimento := _data(achado.group(1)):
            dados["vencimento"] = vencimento
    if "valor" not in dados and (achado := RE_VALOR.search(texto)):
        dados["valor"] = Decimal(achado.group(1).replace(".", "").replace(",", "."))
    if achado := RE_BENEFICIARIO.search(texto):
        dados["fornecedor"] = achado.group(1).strip()[:100]
    elif achado := RE_ORGAO.search(texto):
        dados["fornecedor"] = _nome_proprio(achado.group(1))[:100]
    categoria = adivinhar_categoria(dados.get("fornecedor")) or dados.get("categoria") or adivinhar_categoria(texto)
    if categoria:
        dados["categoria"] = categoria
    if dados.get("fornecedor") or categoria:
        from .models import ContaEscritorio

        tipo = ContaEscritorio.Categoria(categoria).label if categoria else ""
        if categoria == "IMPOSTOS" and (tributo := RE_TRIBUTO.search(texto)):
            tipo = tributo.group(1).strip().capitalize() if tributo.group(1).startswith("TAXA") else tributo.group(1)
        partes = [tipo, dados.get("fornecedor", "").split(" ")[0]]
        dados["descricao"] = " - ".join(p for p in partes if p)[:150]
    return dados
