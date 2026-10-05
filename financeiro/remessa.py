"""Arquivo de remessa CNAB 240 da cobranca Caixa (SIGCB), conforme o manual 67.118 v031 (fev/2024).

Cada pendencia vira um par de segmentos P (dados do titulo) e Q (dados do pagador) com o codigo
de movimento correspondente, mais o segmento R (multa) quando o convenio cobra multa. Os boletos ja existem no banco (foram emitidos pela construtora), entao
a remessa so leva alteracoes: nunca entrada de titulo.

So o leiaute de codigo de beneficiario com 7 digitos (a partir de 1100000) e suportado: versao
107 no header de arquivo e 067 no header de lote (notas G007, G019 e G030 do manual).
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import ConvenioCaixa, PendenciaCaixa, RemessaCaixa
from .pendencias import dados_titulo
from .validadores import so_digitos

BANCO = "104"
VERSAO_ARQUIVO = "107"
VERSAO_LOTE = "067"
LOTE = "0001"

# Nota C004 (codigos de movimento da remessa).
MOVIMENTO = {
    PendenciaCaixa.Tipo.BAIXA: "02",
    PendenciaCaixa.Tipo.ALTERAR_VENCIMENTO: "06",
    PendenciaCaixa.Tipo.ALTERAR_VALOR: "47",
    PendenciaCaixa.Tipo.ALTERAR_PAGADOR: "31",
}
# Nota C009: para '31' a identificacao da emissao tem de ser '4' (banco reemite) ou '5' (banco nao reemite).
EMISSAO = {"31": "5"}
EMISSAO_BENEFICIARIO = "2"


class ConvenioIncompleto(ValueError):
    pass


@dataclass(frozen=True)
class Titulo:
    nosso_numero: str
    documento: str
    vencimento: date
    valor: Decimal
    emissao: date | None
    pagador: dict


def _alfa(texto, tamanho: int) -> str:
    """Campo X: maiusculas, sem acento, sem cedilha e sem caracteres especiais (item 3.2 do manual)."""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^A-Z0-9 ]", " ", texto.upper())
    return re.sub(r" +", " ", texto).strip()[:tamanho].ljust(tamanho)


def _num(valor, tamanho: int) -> str:
    """Campo 9: so digitos, alinhado a direita com zeros."""
    if isinstance(valor, Decimal):
        valor = int((valor * 100).quantize(Decimal("1")))
    if isinstance(valor, date):
        valor = valor.strftime("%d%m%Y")
    texto = so_digitos(str(valor))
    if len(texto) > tamanho:
        raise ValueError(f"{valor!r} não cabe em {tamanho} posições")
    return texto.zfill(tamanho)


def registro(*campos) -> str:
    """Monta uma linha de 240 posicoes a partir de (inicio, fim, tipo, valor), conferindo as posicoes do manual.

    Tipo '9' numerico, 'X' alfanumerico (tratado por _alfa) ou 'L' texto literal.
    """
    linha = ""
    for inicio, fim, tipo, valor in campos:
        if inicio != len(linha) + 1:
            raise ValueError(f"campo {inicio}-{fim} fora de ordem (linha está em {len(linha)})")
        tamanho = fim - inicio + 1
        if tipo == "9":
            linha += _num(valor, tamanho)
        elif tipo == "L":  # literal do manual, sem tratamento
            linha += valor.ljust(tamanho)[:tamanho]
        else:
            linha += _alfa(valor, tamanho)
    if len(linha) != 240:
        raise ValueError(f"registro com {len(linha)} posições")
    return linha


def _inscricao(cpf_cnpj: str) -> tuple[str, str]:
    digitos = so_digitos(cpf_cnpj)
    return ("1" if len(digitos) == 11 else "2"), digitos


def _controle(tipo: str, sequencial=None, segmento=None, movimento=None):
    campos = [(1, 3, "9", BANCO), (4, 7, "9", LOTE), (8, 8, "9", tipo)]
    if sequencial is not None:
        campos += [(9, 13, "9", sequencial), (14, 14, "X", segmento), (15, 15, "X", ""), (16, 17, "9", movimento)]
    return campos


def header_arquivo(conv: ConvenioCaixa, nsa: int, agora: datetime, teste: bool) -> str:
    tipo, inscricao = _inscricao(conv.cpf_cnpj)
    return registro(
        (1, 3, "9", BANCO), (4, 7, "9", "0000"), (8, 8, "9", "0"),
        (9, 17, "X", ""),
        (18, 18, "9", tipo), (19, 32, "9", inscricao),
        (33, 52, "9", 0),
        (53, 57, "9", conv.agencia), (58, 58, "X", conv.dv_agencia),
        (59, 65, "9", conv.codigo_beneficiario),
        (66, 71, "9", 0), (72, 72, "9", 0),
        (73, 102, "X", conv.nome),
        (103, 132, "X", "C ECON FEDERAL"),
        (133, 142, "X", ""),
        (143, 143, "9", "1"),
        (144, 151, "9", agora.date()), (152, 157, "9", agora.strftime("%H%M%S")),
        (158, 163, "9", nsa),
        (164, 166, "9", VERSAO_ARQUIVO),
        (167, 171, "9", 0),
        (172, 191, "X", ""),
        (192, 211, "L", "REMESSA-TESTE" if teste else ""),
        (212, 215, "X", ""),
        (216, 240, "X", ""),
    )


def header_lote(conv: ConvenioCaixa, nsa: int, agora: datetime) -> str:
    tipo, inscricao = _inscricao(conv.cpf_cnpj)
    return registro(
        *_controle("1"),
        (9, 9, "X", "R"), (10, 11, "9", "01"), (12, 13, "9", 0), (14, 16, "9", VERSAO_LOTE),
        (17, 17, "X", ""),
        (18, 18, "9", tipo), (19, 33, "9", inscricao),
        (34, 40, "9", conv.codigo_beneficiario),
        (41, 53, "9", 0),
        (54, 58, "9", conv.agencia), (59, 59, "X", conv.dv_agencia),
        (60, 65, "9", 0),  # 14.1: zeros para codigo de beneficiario com 7 digitos
        (66, 72, "9", 0), (73, 73, "9", 0),
        (74, 103, "X", conv.nome),
        (104, 143, "X", ""), (144, 183, "X", ""),
        (184, 191, "9", nsa), (192, 199, "9", agora.date()),
        (200, 207, "9", 0),
        (208, 240, "X", ""),
    )


def segmento_p(conv: ConvenioCaixa, seq: int, movimento: str, t: Titulo) -> str:
    emissao = t.emissao if t.emissao and t.emissao <= t.vencimento else t.vencimento
    return registro(
        *_controle("3", seq, "P", movimento),
        (18, 22, "9", conv.agencia), (23, 23, "X", conv.dv_agencia),
        (24, 30, "9", conv.codigo_beneficiario),
        (31, 37, "9", 0), (38, 39, "9", 0),
        (40, 40, "9", 0),
        (41, 42, "9", t.nosso_numero[:2]), (43, 57, "9", t.nosso_numero[2:]),
        (58, 58, "9", "1"),  # cobranca simples
        (59, 59, "9", "1"),  # registrada
        (60, 60, "X", "2"),  # escritural
        (61, 61, "9", EMISSAO.get(movimento, EMISSAO_BENEFICIARIO)),
        (62, 62, "X", "0"),  # entrega pelo beneficiario
        (63, 73, "X", t.documento), (74, 77, "X", ""),
        (78, 85, "9", t.vencimento), (86, 100, "9", t.valor),
        (101, 105, "9", 0), (106, 106, "X", "0"),
        (107, 108, "9", conv.especie_titulo),
        (109, 109, "X", "N"),
        (110, 117, "9", emissao),
        (118, 118, "9", conv.juros_codigo),
        (119, 126, "9", 0),  # data dos juros: a Caixa assume vencimento + 1 dia
        (127, 141, "9", conv.juros_valor if conv.juros_codigo != "3" else 0),
        (142, 142, "9", "0"), (143, 150, "9", 0), (151, 165, "9", 0),  # sem desconto
        (166, 180, "9", 0), (181, 195, "9", 0),  # IOF e abatimento
        (196, 220, "X", t.nosso_numero),
        (221, 221, "9", conv.protesto_codigo),
        (222, 223, "9", conv.protesto_dias if conv.protesto_codigo == "1" else 0),
        (224, 224, "9", "1"), (225, 227, "9", conv.devolucao_dias),  # baixar/devolver
        (228, 229, "9", "09"),
        (230, 239, "9", 0),
        (240, 240, "X", "1"),  # nao aceita pagamento divergente
    )


def segmento_q(seq: int, movimento: str, t: Titulo) -> str:
    p = t.pagador
    tipo, inscricao = _inscricao(p.get("cpf_cnpj"))
    cep = so_digitos(p.get("cep")).ljust(8, "0")
    return registro(
        *_controle("3", seq, "Q", movimento),
        (18, 18, "9", tipo), (19, 33, "9", inscricao),
        (34, 73, "X", p.get("nome")),
        (74, 113, "X", p.get("endereco")), (114, 128, "X", p.get("bairro")),
        (129, 133, "9", cep[:5]), (134, 136, "9", cep[5:]),
        (137, 151, "X", p.get("cidade")), (152, 153, "X", p.get("uf")),
        (154, 154, "9", 0), (155, 169, "9", 0), (170, 209, "X", ""),  # sem sacador/avalista
        (210, 212, "9", 0), (213, 232, "X", ""),
        (233, 240, "X", ""),
    )


def segmento_r(conv: ConvenioCaixa, seq: int, movimento: str, t: Titulo) -> str:
    return registro(
        *_controle("3", seq, "R", movimento),
        (18, 18, "9", 0), (19, 26, "9", 0), (27, 41, "9", 0),  # sem desconto 2
        (42, 42, "9", 0), (43, 50, "9", 0), (51, 65, "9", 0),  # sem desconto 3
        (66, 66, "X", conv.multa_codigo),
        (67, 74, "9", t.vencimento + timedelta(days=1)),
        (75, 89, "9", conv.multa_valor),
        (90, 99, "X", ""), (100, 139, "X", ""), (140, 179, "X", ""),
        (180, 229, "X", ""), (230, 240, "X", ""),
    )


def trailer_lote(qtd_registros: int, qtd_titulos: int, total: Decimal) -> str:
    return registro(
        *_controle("5"),
        (9, 17, "X", ""),
        (18, 23, "9", qtd_registros),
        (24, 29, "9", qtd_titulos), (30, 46, "9", total),
        (47, 52, "9", 0), (53, 69, "9", 0), (70, 75, "9", 0), (76, 92, "9", 0),
        (93, 123, "X", ""), (124, 240, "X", ""),
    )


def trailer_arquivo(qtd_registros: int) -> str:
    return registro(
        (1, 3, "9", BANCO), (4, 7, "9", "9999"), (8, 8, "9", "9"),
        (9, 17, "X", ""),
        (18, 23, "9", 1), (24, 29, "9", qtd_registros),
        (30, 35, "X", ""), (36, 240, "X", ""),
    )


def montar_arquivo(conv: ConvenioCaixa, itens: list[tuple[str, Titulo]], nsa: int, agora: datetime, teste: bool) -> str:
    """Texto do arquivo (uma linha de 240 posicoes por registro, CRLF) para os pares (movimento, titulo)."""
    detalhes = []
    for movimento, titulo in itens:
        detalhes.append(segmento_p(conv, len(detalhes) + 1, movimento, titulo))
        detalhes.append(segmento_q(len(detalhes) + 1, movimento, titulo))
        if conv.multa_codigo != "0" and movimento != MOVIMENTO[PendenciaCaixa.Tipo.BAIXA]:
            detalhes.append(segmento_r(conv, len(detalhes) + 1, movimento, titulo))
    total = sum((t.valor for _, t in itens), Decimal("0"))
    linhas = [
        header_arquivo(conv, nsa, agora, teste),
        header_lote(conv, nsa, agora),
        *detalhes,
        trailer_lote(len(detalhes) + 2, len(itens), total),
    ]
    linhas.append(trailer_arquivo(len(linhas) + 1))
    return "\r\n".join(linhas) + "\r\n"


# ---------------------------------------------------------------- A partir das pendencias


def titulo_da_pendencia(p: PendenciaCaixa) -> Titulo:
    dados = dados_titulo(p.parcela) if p.parcela else p.titulo
    emissao = dados.get("emissao")
    return Titulo(
        nosso_numero=p.nosso_numero,
        documento=dados.get("documento") or "",
        vencimento=date.fromisoformat(dados["vencimento"]),
        valor=Decimal(dados["valor"]),
        emissao=date.fromisoformat(emissao) if emissao else None,
        pagador=dados.get("pagador") or {},
    )


def motivo_para_nao_enviar(p: PendenciaCaixa, conv: ConvenioCaixa, hoje: date) -> str | None:
    if not re.fullmatch(r"14\d{15}", p.nosso_numero or ""):
        return "nosso número fora do padrão da Caixa (17 dígitos começando com 14)"
    if not p.parcela and not p.titulo:
        return "parcela excluída antes de existir o arquivo de remessa: faça no e-Cobrança"
    if p.tipo == PendenciaCaixa.Tipo.ALTERAR_PAGADOR and p.antes.get("cpf_cnpj") != p.depois.get("cpf_cnpj"):
        return "a Caixa não permite alterar o CPF/CNPJ do pagador: baixe o boleto e emita outro"
    titulo = titulo_da_pendencia(p)
    if p.tipo == PendenciaCaixa.Tipo.ALTERAR_VENCIMENTO and titulo.vencimento < hoje:
        # Retorno 26/17 da Caixa: nao aceita vencimento novo que ja passou.
        return "o novo vencimento já passou: a Caixa não aceita alterar para uma data anterior a hoje"
    pagador = titulo.pagador
    if not pagador.get("nome") or not pagador.get("cpf_cnpj"):
        return "cliente sem CPF/CNPJ no cadastro (obrigatório na remessa)"
    if so_digitos(pagador["cpf_cnpj"]) == so_digitos(conv.cpf_cnpj):
        return "CPF/CNPJ do pagador igual ao do beneficiário"
    return None


@transaction.atomic
def gerar_remessa(pendencias, usuario=None, agora=None):
    """Gera o arquivo com as pendencias que podem ir por remessa.

    Devolve (remessa ou None, [(pendencia, motivo)] das que ficaram de fora). Em producao as
    pendencias enviadas passam a ENVIADA; em teste continuam pendentes (a Caixa nao altera nada).
    """
    conv = ConvenioCaixa.objects.select_for_update().first()
    if conv is None or conv.faltando():
        faltando = conv.faltando() if conv else ["todos os dados"]
        raise ConvenioIncompleto("Preencha no convênio Caixa: " + ", ".join(faltando) + ".")

    agora = timezone.localtime(agora or timezone.now())
    enviar, ignoradas = [], []
    for p in pendencias.filter(status=PendenciaCaixa.Status.PENDENTE).select_related("parcela__financiamento__cliente"):
        motivo = motivo_para_nao_enviar(p, conv, agora.date())
        if motivo:
            ignoradas.append((p, motivo))
        else:
            enviar.append(p)
    if not enviar:
        return None, ignoradas

    nsa = (RemessaCaixa.objects.aggregate(m=Max("nsa"))["m"] or 0) + 1
    teste = conv.ambiente_teste
    itens = [(MOVIMENTO[p.tipo], titulo_da_pendencia(p)) for p in enviar]
    remessa = RemessaCaixa.objects.create(
        nsa=nsa,
        criado_por=usuario,
        teste=teste,
        quantidade=len(itens),
        conteudo=montar_arquivo(conv, itens, nsa, agora, teste),
    )
    for p in enviar:
        p.remessa = remessa
        if not teste:
            p.status, p.enviado_em, p.enviado_por = PendenciaCaixa.Status.ENVIADA, agora, usuario
        p.save()
    return remessa, ignoradas
