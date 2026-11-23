"""Codigo de barras, linha digitavel e PDF do boleto da Caixa (SIGCB), conforme o manual 67.119 v014.

O boleto e emitido pelo beneficiario ("sistema proprio"): o nosso numero ja existe (veio do extrato da
Caixa) e o sistema so imprime. Antes de usar com os clientes a Caixa exige homologar uma massa de teste
de 10 a 20 boletos com todos os DVs gerais (1 a 9) e todos os DVs do campo livre (0 a 9) - item 1.1
do manual; `massa_de_teste` escolhe as parcelas.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from .extrato import EM_ABERTO
from .validadores import so_digitos

BANCO = "104"
MOEDA = "9"
DATA_BASE = date(1997, 10, 7)
LOCAL_PAGAMENTO = "EM TODA A REDE BANCÁRIA E SEUS CORRESPONDENTES ATÉ O VALOR LIMITE"
# Nota C015 do manual CNAB 240 (codigo da especie -> sigla impressa no boleto).
ESPECIES = {
    "01": "CH", "02": "DM", "03": "DMI", "04": "DS", "05": "DSI", "06": "DR", "07": "LC", "08": "NCC",
    "09": "NCE", "10": "NCI", "11": "NCR", "12": "NP", "13": "NPR", "14": "TM", "15": "TS", "16": "NS",
    "17": "RC", "18": "FAT", "19": "ND", "20": "AP", "21": "ME", "22": "PC", "23": "NF", "24": "DD",
    "25": "CPR", "99": "OU",
}


class BoletoInvalido(ValueError):
    pass


# ---------------------------------------------------------------- Calculos (anexos do manual)


def _modulo11(numero: str) -> int:
    """11 - (soma ponderada 2..9 da direita para a esquerda mod 11); o chamador trata 10 e 11."""
    soma = sum(int(d) * (2 + i % 8) for i, d in enumerate(reversed(numero)))
    return 11 - soma % 11


def dv_nosso_numero(nosso_numero: str) -> int:
    """Anexo IV: modulo 11 sobre as 17 posicoes; resultado maior que 9 vira 0."""
    dv = _modulo11(nosso_numero)
    return 0 if dv > 9 else dv


def dv_codigo_beneficiario(codigo: str) -> int:
    """Item 4.2.3.2 / Anexo VI: modulo 11 sobre o codigo; resultado maior que 9 vira 0."""
    dv = _modulo11(codigo)
    return 0 if dv > 9 else dv


def dv_campo_livre(campo: str) -> int:
    """Anexo III: modulo 11 sobre as 24 primeiras posicoes do campo livre; maior que 9 vira 0."""
    dv = _modulo11(campo)
    return 0 if dv > 9 else dv


def dv_geral(codigo_sem_dv: str) -> int:
    """Anexo I: modulo 11 sobre as 43 posicoes (sem a 5a); 0 ou maior que 9 vira 1."""
    dv = _modulo11(codigo_sem_dv)
    return 1 if dv in (0, 10, 11) else dv


def dv_modulo10(numero: str) -> int:
    """Anexo V: pesos 2 e 1 alternados da direita para a esquerda, somando os algarismos do produto."""
    soma = 0
    for i, d in enumerate(reversed(numero)):
        produto = int(d) * (2 if i % 2 == 0 else 1)
        soma += produto // 10 + produto % 10
    return (10 - soma % 10) % 10


def fator_vencimento(vencimento: date) -> int:
    """Anexo II: dias desde 07/10/1997; depois de 9999 volta para 1000 (22/02/2025 = 1000)."""
    fator = (vencimento - DATA_BASE).days
    if fator < 1000:
        raise BoletoInvalido("vencimento anterior a 03/07/2000")
    while fator > 9999:
        fator -= 9000
    return fator


def campo_livre(codigo_beneficiario: str, nosso_numero: str) -> str:
    """Anexo III, para codigo de beneficiario de 7 digitos (a partir de 1100000): 25 posicoes."""
    nn = nosso_numero
    sem_dv = codigo_beneficiario + nn[2:5] + nn[0] + nn[5:8] + nn[1] + nn[8:17]
    return sem_dv + str(dv_campo_livre(sem_dv))


def codigo_de_barras(codigo_beneficiario: str, nosso_numero: str, vencimento: date, valor: Decimal) -> str:
    """44 posicoes (item 4.2.10.1.1)."""
    if not re.fullmatch(r"\d{7}", codigo_beneficiario or "") or int(codigo_beneficiario) < 1100000:
        raise BoletoInvalido("código do beneficiário precisa ter 7 dígitos (a partir de 1100000)")
    if not re.fullmatch(r"1\d{16}", nosso_numero or ""):
        raise BoletoInvalido("nosso número precisa ter 17 dígitos começando com 1 (registrada)")
    centavos = int((valor * 100).quantize(Decimal("1")))
    if not 0 < centavos <= 999999999:
        raise BoletoInvalido("valor precisa ser maior que zero e até R$ 9.999.999,99")
    resto = f"{fator_vencimento(vencimento):04d}{centavos:010d}" + campo_livre(codigo_beneficiario, nosso_numero)
    return BANCO + MOEDA + str(dv_geral(BANCO + MOEDA + resto)) + resto


def linha_digitavel(codigo: str) -> str:
    """Anexo V: cinco campos, os tres primeiros com DV modulo 10."""
    livre = codigo[19:44]
    c1, c2, c3 = codigo[0:4] + livre[0:5], livre[5:15], livre[15:25]
    c1, c2, c3 = (c + str(dv_modulo10(c)) for c in (c1, c2, c3))
    return f"{c1[:5]}.{c1[5:]} {c2[:5]}.{c2[5:]} {c3[:5]}.{c3[5:]} {codigo[4]} {codigo[5:19]}"


def barras_2de5_intercalado(codigo: str) -> list[tuple[bool, int]]:
    """Sequencia de (barra?, largura em modulos) do "2 de 5 intercalado", com inicio e fim (estreita=1, larga=3)."""
    padroes = ["nnwwn", "wnnnw", "nwnnw", "wwnnn", "nnwnw", "wnwnn", "nwwnn", "nnnww", "wnnwn", "nwnwn"]
    sequencia = [(True, 1), (False, 1), (True, 1), (False, 1)]
    for i in range(0, len(codigo), 2):
        barras, espacos = padroes[int(codigo[i])], padroes[int(codigo[i + 1])]
        for b, e in zip(barras, espacos):
            sequencia += [(True, 3 if b == "w" else 1), (False, 3 if e == "w" else 1)]
    return sequencia + [(True, 3), (False, 1), (True, 1)]


# ---------------------------------------------------------------- Dados do boleto


@dataclass
class Boleto:
    beneficiario: str
    beneficiario_cpf_cnpj: str
    beneficiario_endereco: str
    agencia_codigo: str
    nosso_numero: str  # 17 digitos + "-" + DV
    documento: str
    data_documento: date | None
    data_processamento: date
    especie: str
    vencimento: date
    valor: Decimal
    pagador: dict
    instrucoes: list[str]
    codigo_barras: str
    linha_digitavel: str

    @property
    def dv_geral(self) -> int:
        return int(self.codigo_barras[4])

    @property
    def dv_campo_livre(self) -> int:
        return int(self.codigo_barras[43])


def _brl(valor: Decimal) -> str:
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def instrucoes(conv, vencimento: date) -> list[str]:
    """Mesmas condicoes registradas no titulo (item 4.2.6.1): juros, multa e prazo de recebimento."""
    dia_seguinte = (vencimento + timedelta(days=1)).strftime("%d/%m/%Y")
    linhas = []
    if conv.juros_codigo == "2":
        linhas.append(f"JUROS DE {_brl(conv.juros_valor)}% AO MÊS (DIAS CORRIDOS) A PARTIR DE {dia_seguinte}")
    elif conv.juros_codigo == "1":
        linhas.append(f"JUROS DE R$ {_brl(conv.juros_valor)} POR DIA DE ATRASO A PARTIR DE {dia_seguinte}")
    if conv.multa_codigo == "1":
        linhas.append(f"MULTA DE R$ {_brl(conv.multa_valor)} A PARTIR DE {dia_seguinte}")
    elif conv.multa_codigo == "2":
        linhas.append(f"MULTA DE {_brl(conv.multa_valor)}% A PARTIR DE {dia_seguinte}")
    if conv.protesto_codigo == "1" and conv.protesto_dias:
        linhas.append(f"SUJEITO A PROTESTO APÓS {conv.protesto_dias} DIAS DE ATRASO")
    elif conv.devolucao_dias:
        linhas.append(f"NÃO RECEBER APÓS {conv.devolucao_dias} DIAS DE ATRASO")
    return linhas


def endereco_beneficiario(conv) -> str:
    partes = [conv.endereco, conv.bairro, " - ".join(p for p in (conv.cidade, conv.uf) if p)]
    texto = ", ".join(p for p in partes if p)
    return f"{texto} - CEP {conv.cep}" if conv.cep else texto


def boleto_da_parcela(parcela, conv, hoje: date) -> Boleto:
    if not parcela.nosso_numero:
        raise BoletoInvalido("parcela sem nosso número")
    if parcela.data_pagamento:
        raise BoletoInvalido("parcela já paga")
    if parcela.situacao_banco != EM_ABERTO:
        raise BoletoInvalido("o boleto não está em aberto na Caixa")
    fin = parcela.financiamento
    cliente = fin.cliente
    if not cliente.nome or not cliente.cpf_cnpj:
        raise BoletoInvalido("cliente sem CPF/CNPJ no cadastro (obrigatório no boleto)")
    codigo = codigo_de_barras(conv.codigo_beneficiario, parcela.nosso_numero, parcela.vencimento, parcela.valor)
    documento = fin.numero_documento or ""
    if parcela.numero:
        documento = f"{documento} /{parcela.numero:03d}/{(fin.total_parcelas or 0):03d}".strip()
    return Boleto(
        beneficiario=conv.nome,
        beneficiario_cpf_cnpj=conv.cpf_cnpj,
        beneficiario_endereco=endereco_beneficiario(conv),
        agencia_codigo=f"{conv.agencia} / {conv.codigo_beneficiario}",  # sem o DV, como no carne da Caixa
        nosso_numero=f"{parcela.nosso_numero}-{dv_nosso_numero(parcela.nosso_numero)}",
        documento=documento,
        data_documento=fin.data_entrada,
        data_processamento=hoje,
        especie=ESPECIES.get(conv.especie_titulo, conv.especie_titulo),
        vencimento=parcela.vencimento,
        valor=parcela.valor,
        pagador=cliente.dados_pagador(),
        instrucoes=instrucoes(conv, parcela.vencimento),
        codigo_barras=codigo,
        linha_digitavel=linha_digitavel(codigo),
    )


def massa_de_teste(parcelas, conv, hoje: date, minimo=10, maximo=20):
    """Escolhe de 10 a 20 boletos que cubram os DVs gerais 1-9 e os DVs do campo livre 0-9 (item 1.1).

    Devolve (boletos, faltando) - faltando lista os DVs que nenhuma parcela disponivel cobre.
    """
    candidatos = []
    for p in parcelas:
        try:
            candidatos.append(boleto_da_parcela(p, conv, hoje))
        except BoletoInvalido:
            continue
    falta_geral, falta_livre = set(range(1, 10)), set(range(10))
    escolhidos = []
    while candidatos and len(escolhidos) < maximo and (falta_geral or falta_livre):
        melhor = max(candidatos, key=lambda b: (b.dv_geral in falta_geral) + (b.dv_campo_livre in falta_livre))
        if not (melhor.dv_geral in falta_geral or melhor.dv_campo_livre in falta_livre):
            break
        escolhidos.append(melhor)
        candidatos.remove(melhor)
        falta_geral.discard(melhor.dv_geral)
        falta_livre.discard(melhor.dv_campo_livre)
    while candidatos and len(escolhidos) < minimo:
        escolhidos.append(candidatos.pop(0))
    faltando = [f"DV geral {d}" for d in sorted(falta_geral)] + [f"DV do campo livre {d}" for d in sorted(falta_livre)]
    return sorted(escolhidos, key=lambda b: (b.dv_geral, b.dv_campo_livre)), faltando


def clientes_que_completam(parcelas, conv, boletos):
    """Clientes sem CPF/CNPJ cujas parcelas cobririam os DVs que faltam na massa de teste (menos clientes primeiro)."""
    falta_geral = set(range(1, 10)) - {b.dv_geral for b in boletos}
    falta_livre = set(range(10)) - {b.dv_campo_livre for b in boletos}
    dvs = {}
    for p in parcelas:
        cliente = p.financiamento.cliente
        if cliente.cpf_cnpj or not p.nosso_numero:
            continue
        try:
            codigo = codigo_de_barras(conv.codigo_beneficiario, p.nosso_numero, p.vencimento, p.valor)
        except BoletoInvalido:
            continue
        geral, livre = dvs.setdefault(cliente, (set(), set()))
        geral.add(int(codigo[4]))
        livre.add(int(codigo[-1]))
    sugeridos = []
    while (falta_geral or falta_livre) and dvs:
        cliente = max(dvs, key=lambda c: (len(dvs[c][0] & falta_geral) + len(dvs[c][1] & falta_livre), -c.pk))
        geral, livre = dvs.pop(cliente)
        if not (geral & falta_geral or livre & falta_livre):
            break
        sugeridos.append(cliente)
        falta_geral -= geral
        falta_livre -= livre
    return sugeridos


# ---------------------------------------------------------------- PDF (A4: recibo do pagador em cima, ficha embaixo)


def gerar_pdf(boletos: list[Boleto]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Boletos")
    for boleto in boletos:
        _pagina(pdf, boleto, A4, mm)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _pagina(pdf, b: Boleto, pagina, mm):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    largura = 190 * mm
    x0 = (pagina[0] - largura) / 2
    pagador = b.pagador
    doc = b.data_documento.strftime("%d/%m/%Y") if b.data_documento else ""
    proc = b.data_processamento.strftime("%d/%m/%Y")
    venc = b.vencimento.strftime("%d/%m/%Y")
    valor = "R$ " + _brl(b.valor)
    linha_pagador = f"{pagador.get('nome', '')}"
    endereco_pagador = " - ".join(
        p for p in [pagador.get("endereco"), pagador.get("bairro"), pagador.get("cidade")] if p
    )

    def caixa(x, y, w, h, rotulo, valor="", tamanho=8, negrito=False, alinhar="esquerda"):
        pdf.setLineWidth(0.3 * mm / 2)
        pdf.rect(x, y, w, h)
        pdf.setFont("Helvetica", 5.5)
        pdf.drawString(x + 1 * mm, y + h - 2.3 * mm, rotulo)
        pdf.setFont("Helvetica-Bold" if negrito else "Helvetica", tamanho)
        if alinhar == "direita":
            pdf.drawRightString(x + w - 1.5 * mm, y + 1.6 * mm, valor)
        else:
            pdf.drawString(x + 1.5 * mm, y + 1.6 * mm, valor)

    def cabecalho(y):
        """Identificacao do banco, 104-0 (5 mm, fios de 1,2 mm) e linha digitavel (3,5 a 4 mm) - item 4.2.1."""
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(x0, y + 1.5 * mm, "CAIXA")
        pdf.setLineWidth(1.2 * mm)
        pdf.line(x0 + 23 * mm, y, x0 + 23 * mm, y + 8 * mm)
        pdf.line(x0 + 47 * mm, y, x0 + 47 * mm, y + 8 * mm)
        pdf.setFont("Helvetica-Bold", 19)  # algarismos com ~5 mm de altura
        pdf.drawCentredString(x0 + 35 * mm, y + 1.5 * mm, "104-0")
        espaco = largura - 49 * mm
        tamanho = 14  # algarismos com ~3,6 mm de altura
        while tamanho > 9 and stringWidth(b.linha_digitavel, "Helvetica-Bold", tamanho) > espaco:
            tamanho -= 0.5
        pdf.setFont("Helvetica-Bold", tamanho)
        pdf.drawRightString(x0 + largura, y + 1.5 * mm, b.linha_digitavel)
        pdf.setLineWidth(0.3 * mm)
        pdf.line(x0, y, x0 + largura, y)

    # ----- Recibo do pagador (item 3, modelo II)
    h = 8 * mm
    topo = pagina[1] - 18 * mm
    cabecalho(topo)
    col = largura - 45 * mm
    y = topo - h
    caixa(x0, y, col - 40 * mm, h, "Beneficiário", b.beneficiario)
    caixa(x0 + col - 40 * mm, y, 40 * mm, h, "CPF/CNPJ", b.beneficiario_cpf_cnpj)
    caixa(x0 + col, y, 45 * mm, h, "Agência / Código do Beneficiário", b.agencia_codigo, alinhar="direita")
    y -= h
    caixa(x0, y, largura, h, "Endereço do Beneficiário", b.beneficiario_endereco, tamanho=7)
    y -= h
    w = col / 4
    caixa(x0, y, w, h, "Data do documento", doc)
    caixa(x0 + w, y, w, h, "Nr. do documento", b.documento)
    caixa(x0 + 2 * w, y, w, h, "Aceite", "N")
    caixa(x0 + 3 * w, y, w, h, "Data do processamento", proc)
    caixa(x0 + col, y, 45 * mm, h, "Nosso Número", b.nosso_numero, alinhar="direita")
    y -= 4 * h
    pdf.rect(x0, y, largura, 4 * h)
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(x0 + 1 * mm, y + 4 * h - 2.3 * mm, "Instruções (Texto de Responsabilidade do Beneficiário):")
    pdf.setFont("Helvetica", 7.5)
    for i, texto in enumerate(b.instrucoes):
        pdf.drawString(x0 + 1.5 * mm, y + 4 * h - (6.5 + 4 * i) * mm, texto)
    y -= h
    caixa(x0, y, col, h, "Pagador", linha_pagador)
    caixa(x0 + col, y, 45 * mm, h, "CPF/CNPJ", pagador.get("cpf_cnpj", ""), alinhar="direita")
    y -= h
    caixa(x0, y, col, h, "Endereço do Pagador", endereco_pagador, tamanho=7)
    caixa(x0 + col, y, 20 * mm, h, "UF", pagador.get("uf", ""))
    caixa(x0 + col + 20 * mm, y, 25 * mm, h, "CEP", pagador.get("cep", ""))
    y -= h
    caixa(x0, y, w, h, "Carteira", "RG")
    caixa(x0 + w, y, w, h, "Espécie", b.especie)
    caixa(x0 + 2 * w, y, w, h, "Vencimento", venc, negrito=True)
    caixa(x0 + 3 * w, y, w, h, "Valor do Documento", valor, negrito=True)
    caixa(x0 + col, y, 45 * mm, h, "Valor Cobrado", "")
    y -= 4 * mm
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawRightString(x0 + largura, y, "Autenticação Mecânica - Recibo do Pagador")
    pdf.setFont("Helvetica", 6.5)
    sac = [
        "SAC CAIXA: 0800 726 0101 (informações, reclamações, sugestões e elogios)",
        "Para pessoas com deficiência auditiva ou de fala: 0800 726 2492",
        "Ouvidoria: 0800 725 7474",
        "caixa.gov.br",
    ]
    for i, texto in enumerate(sac):
        pdf.drawString(x0, y - i * 3 * mm, texto)

    # ----- Linha de corte
    corte = 125 * mm
    pdf.setDash(2, 2)
    pdf.setLineWidth(0.2)
    pdf.line(x0 - 5 * mm, corte, x0 + largura + 5 * mm, corte)
    pdf.setDash()
    pdf.setFont("Helvetica", 5.5)
    pdf.drawRightString(x0 + largura, corte + 1 * mm, "Corte na linha pontilhada")

    # ----- Ficha de compensacao (item 4, modelo I), 108 mm de altura
    base = corte - 108 * mm
    topo = corte - 12 * mm
    cabecalho(topo)
    col = largura - 50 * mm
    y = topo - h
    caixa(x0, y, col, h, "Local de pagamento", LOCAL_PAGAMENTO, tamanho=7)
    caixa(x0 + col, y, 50 * mm, h, "Vencimento", venc, negrito=True, alinhar="direita")
    y -= h
    pdf.rect(x0, y, col, h)
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(x0 + 1 * mm, y + h - 2.3 * mm, "Beneficiário")
    pdf.setFont("Helvetica", 7)
    pdf.drawString(x0 + 1.5 * mm, y + 3.2 * mm, f"{b.beneficiario} - CNPJ {b.beneficiario_cpf_cnpj}")
    pdf.setFont("Helvetica", 6)
    pdf.drawString(x0 + 1.5 * mm, y + 0.8 * mm, b.beneficiario_endereco)
    caixa(x0 + col, y, 50 * mm, h, "Agência / Código do Beneficiário", b.agencia_codigo, alinhar="direita")
    y -= h
    w = col / 5
    caixa(x0, y, w, h, "Data do documento", doc)
    caixa(x0 + w, y, w, h, "Nr. do Documento", b.documento, tamanho=7)
    caixa(x0 + 2 * w, y, w, h, "Espécie DOC", b.especie)
    caixa(x0 + 3 * w, y, w, h, "Aceite", "N")
    caixa(x0 + 4 * w, y, w, h, "Data do processamento", proc)
    caixa(x0 + col, y, 50 * mm, h, "Nosso Número", b.nosso_numero, alinhar="direita")
    y -= h
    caixa(x0, y, w, h, "Uso do Banco", "")
    caixa(x0 + w, y, w, h, "Carteira", "RG")
    caixa(x0 + 2 * w, y, w, h, "Espécie Moeda", "R$")
    caixa(x0 + 3 * w, y, w, h, "Qtde moeda", "")
    caixa(x0 + 4 * w, y, w, h, "xValor", "")
    caixa(x0 + col, y, 50 * mm, h, "(=) Valor do Documento", valor, negrito=True, alinhar="direita")
    altura_instr = 3 * h
    y -= altura_instr
    pdf.rect(x0, y, col, altura_instr)
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(x0 + 1 * mm, y + altura_instr - 2.3 * mm, "Instruções (Texto de Responsabilidade do Beneficiário):")
    pdf.setFont("Helvetica", 7.5)
    for i, texto in enumerate(b.instrucoes):
        pdf.drawString(x0 + 1.5 * mm, y + altura_instr - (6.5 + 4 * i) * mm, texto)
    caixa(x0 + col, y + 2 * h, 50 * mm, h, "(-) Desconto/Abatimento", "")
    caixa(x0 + col, y + h, 50 * mm, h, "(+) Juros/Multa", "")
    caixa(x0 + col, y, 50 * mm, h, "(=) Valor Cobrado", "")
    y -= 2 * h
    pdf.rect(x0, y, largura, 2 * h)
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(x0 + 1 * mm, y + 2 * h - 2.3 * mm, "Pagador:")
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(x0 + 12 * mm, y + 2 * h - 2.6 * mm, linha_pagador)
    pdf.drawString(x0 + 12 * mm, y + 2 * h - 6 * mm, endereco_pagador)
    pdf.drawString(x0 + col + 1.5 * mm, y + 2 * h - 2.6 * mm, f"CPF/CNPJ: {pagador.get('cpf_cnpj', '')}")
    pdf.drawString(x0 + col + 1.5 * mm, y + 2 * h - 6 * mm, f"UF: {pagador.get('uf', '')}   CEP: {pagador.get('cep', '')}")
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(x0 + 1 * mm, y + 1.5 * mm, "Sacador/Avalista:")
    pdf.drawString(x0 + col + 1.5 * mm, y + 1.5 * mm, "CPF/CNPJ:")
    pdf.setFont("Helvetica", 5.5)
    pdf.drawRightString(x0 + largura, y - 2.5 * mm, "Autenticação Mecânica - Ficha de Compensação")

    # Codigo de barras: 103 x 13 mm, centro a 12 mm da margem inferior, 5 mm da lateral (item 4.2.10.1)
    barras = barras_2de5_intercalado(b.codigo_barras)
    modulo = 103 * mm / sum(largura_barra for _, largura_barra in barras)
    x = x0 + 5 * mm
    y_barras = base + 12 * mm - 6.5 * mm
    for barra, largura_barra in barras:
        if barra:
            pdf.rect(x, y_barras, largura_barra * modulo, 13 * mm, stroke=0, fill=1)
        x += largura_barra * modulo
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(x0, base - 3 * mm, "Ficha de Compensação")


def nome_arquivo(boletos: list[Boleto]) -> str:
    if len(boletos) == 1:
        return f"boleto_{so_digitos(boletos[0].nosso_numero)}.pdf"
    return "boletos.pdf"
