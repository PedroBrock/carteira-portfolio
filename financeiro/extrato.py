"""Leitura do relatorio "CONSULTA DE TITULOS" exportado da cobranca da Caixa."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

LINHA_TITULO = re.compile(
    r"^ (?P<nosso_numero>\d{17}) +(?P<documento>\S+) +(?:/(?P<parcela>\d{3})/(?P<total>\d{3}) +)?"
    r"(?P<nome>.+?) +(?P<valor>[\d.]+,\d\d) +(?P<vencimento>\d\d\.\d\d\.\d{4}) +"
    r"(?P<situacao>[A-Z][A-Z ]*?) +(?P<ultimo_comando>\d\d\.\d\d\.\d{4}) +(?P<entrada>\d\d\.\d\d\.\d{4})$"
)

LIQUIDADO = "LIQUIDADO"
EM_ABERTO = "EM ABERTO"
BAIXADO_DEVOLUCAO = "BAIXADO POR DEVOLUCAO"


class ExtratoInvalido(ValueError):
    pass


@dataclass(frozen=True)
class TituloExtrato:
    nosso_numero: str
    documento: str
    parcela: int | None
    total_parcelas: int | None
    nome: str
    valor: Decimal
    vencimento: date
    situacao: str
    ultimo_comando: date
    data_entrada: date


def _data(s: str) -> date:
    dia, mes, ano = s.split(".")
    return date(int(ano), int(mes), int(dia))


def ler_extrato(texto: str) -> list[TituloExtrato]:
    titulos = []
    for n, linha in enumerate(texto.splitlines(), start=1):
        linha = linha.rstrip()
        if not linha:
            continue
        m = LINHA_TITULO.match(linha)
        if not m:
            if re.match(r"^ \d{17}", linha):
                raise ExtratoInvalido(f"Linha {n} não reconhecida: {linha.strip()}")
            continue  # cabecalho / separadores
        titulos.append(
            TituloExtrato(
                nosso_numero=m["nosso_numero"],
                documento=m["documento"],
                parcela=int(m["parcela"]) if m["parcela"] else None,
                total_parcelas=int(m["total"]) if m["total"] else None,
                nome=m["nome"],
                valor=Decimal(m["valor"].replace(".", "").replace(",", ".")),
                vencimento=_data(m["vencimento"]),
                situacao=m["situacao"],
                ultimo_comando=_data(m["ultimo_comando"]),
                data_entrada=_data(m["entrada"]),
            )
        )
    return titulos


def ler_arquivo(caminho) -> list[TituloExtrato]:
    with open(caminho, encoding="latin-1") as f:
        return ler_extrato(f.read())
