"""Dados ficticios para demonstracao (comando `popular_demo`).

Gera um extrato "CONSULTA DE TITULOS" no mesmo formato do relatorio da Caixa, com clientes,
contratos e boletos inventados e datas relativas a hoje, para que o painel sempre mostre parcelas
pagas, a vencer, vencidas e baixadas. Nenhum nome, CPF ou valor corresponde a dados reais.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .regras import um_mes_depois

NOMES = [
    "ANA", "BEATRIZ", "BRUNO", "CAMILA", "CARLOS", "DANIELA", "EDUARDO", "FERNANDA", "GABRIEL", "HELENA",
    "IGOR", "JULIANA", "LEONARDO", "LUCAS", "MARIANA", "MATEUS", "NATALIA", "OTAVIO", "PAULA", "RAFAEL",
    "RENATA", "SERGIO", "TATIANE", "VINICIUS",
]
SOBRENOMES = [
    "ALVES", "ARAUJO", "BARBOSA", "CARDOSO", "CASTRO", "CORREIA", "DIAS", "FERREIRA", "GOMES", "LIMA",
    "MARTINS", "MEDEIROS", "MENDES", "MOREIRA", "NOGUEIRA", "PEREIRA", "PINTO", "RAMOS", "RIBEIRO",
    "ROCHA", "SANTANA", "TEIXEIRA", "VIEIRA",
]
EMPREENDIMENTOS = ["Residencial Jardim das Flores", "Loteamento Vale Verde", "Condomínio Parque do Sol"]
RUAS = ["Rua das Palmeiras", "Avenida Brasil", "Rua Sete de Setembro", "Rua do Comércio", "Travessa Esperança"]
BAIRROS = ["Centro", "Jardim América", "Vila Nova", "Santa Luzia", "Boa Vista"]
CIDADE, UF, CEP_BASE = "Cidade Exemplo", "SP", 13000

# Beneficiario ficticio (CNPJ de exemplo, valido so no digito verificador).
CONVENIO_DEMO = {
    "agencia": "0123",
    "dv_agencia": "0",
    "codigo_beneficiario": "7654321",
    "cpf_cnpj": "11.222.333/0001-81",
    "nome": "CONSTRUTORA EXEMPLO LTDA",
}


@dataclass
class TituloDemo:
    nosso_numero: str
    documento: str
    parcela: int | None
    total: int | None
    nome: str
    valor: Decimal
    vencimento: date
    situacao: str
    ultimo_comando: date
    entrada: date


def cpf_ficticio(rng: random.Random) -> str:
    """CPF com digitos verificadores corretos, gerado ao acaso (apenas para demonstracao)."""
    base = [rng.randint(0, 9) for _ in range(9)]
    for tamanho in (9, 10):
        soma = sum(d * (tamanho + 1 - i) for i, d in enumerate(base))
        base.append(0 if soma * 10 % 11 == 10 else soma * 10 % 11)
    return "".join(map(str, base))


def _somar_meses(d: date, n: int) -> date:
    for _ in range(n):
        d = um_mes_depois(d)
    return d


def _brl(valor: Decimal) -> str:
    return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def gerar_clientes(rng: random.Random, quantidade: int) -> list[dict]:
    nomes = set()
    while len(nomes) < quantidade:
        nomes.add(" ".join([rng.choice(NOMES), *rng.sample(SOBRENOMES, 2)]))
    clientes = []
    for nome in sorted(nomes):
        clientes.append({
            "nome": nome,
            "cpf_cnpj": cpf_ficticio(rng),
            "email": nome.split()[0].lower() + "@exemplo.com",
            "telefone": f"(11) 9{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
            "endereco": f"{rng.choice(RUAS)}, {rng.randint(1, 999)}",
            "bairro": rng.choice(BAIRROS),
            "cidade": CIDADE,
            "uf": UF,
            "cep": f"{CEP_BASE + rng.randint(0, 999):05d}{rng.randint(0, 999):03d}",
        })
    return clientes


def gerar_titulos(hoje: date, clientes: list[dict], rng: random.Random) -> list[TituloDemo]:
    titulos = []
    sequencial = iter(range(1, 10**6))

    def nosso_numero():
        return f"14{next(sequencial):015d}"

    for i, cliente in enumerate(clientes):
        total = rng.choice([12, 24, 36, 48, 60])
        valor = Decimal(rng.randint(60, 320) * 10) + Decimal(rng.randint(0, 99)) / 100
        entrada = hoje - timedelta(days=rng.randint(60, 700))
        dia = rng.choice([5, 10, 15, 20, 25])
        primeiro = date(entrada.year, entrada.month, dia)
        primeiro = um_mes_depois(primeiro)
        documento = f"{i + 1:03d}"
        atrasa = rng.random() < 0.25  # alguns clientes deixam de pagar as ultimas parcelas

        if rng.random() < 0.4:  # entrada paga a parte (titulo avulso, sem numero de parcela)
            venc = entrada + timedelta(days=10)
            titulos.append(TituloDemo(
                nosso_numero(), documento, None, None, cliente["nome"], valor * 3, venc, "LIQUIDADO",
                venc - timedelta(days=rng.randint(0, 3)), entrada,
            ))

        for n in range(1, total + 1):
            venc = _somar_meses(primeiro, n - 1)
            emitido = max(entrada, venc - timedelta(days=40))
            if emitido > hoje:
                break  # boletos sao emitidos ate ~40 dias antes do vencimento
            if venc < hoje - timedelta(days=75) or (venc < hoje and not atrasa and rng.random() < 0.85):
                pago = venc + timedelta(days=rng.choice([-3, -1, 0, 0, 0, 2, 5]))
                situacao, comando = "LIQUIDADO", min(pago, hoje)
            elif venc < hoje and atrasa and rng.random() < 0.2:
                situacao, comando = "BAIXADO POR DEVOLUCAO", min(venc + timedelta(days=30), hoje)
            else:
                situacao, comando = "EM ABERTO", emitido
            titulos.append(TituloDemo(
                nosso_numero(), documento, n, total, cliente["nome"], valor, venc, situacao, comando, entrada,
            ))

    # Um boleto baixado antes de vencer (renegociacao): cai em "Baixado - verificar".
    alvo = next(t for t in titulos if t.situacao == "EM ABERTO" and t.vencimento > hoje)
    alvo.situacao, alvo.ultimo_comando = "BAIXADO POR DEVOLUCAO", hoje - timedelta(days=2)
    return titulos


def formatar_extrato(titulos: list[TituloDemo]) -> str:
    """Mesmo leiaute de colunas do relatorio CONSULTA DE TITULOS da Caixa."""
    linhas = [
        "CONSULTA DE TITULOS",
        "",
        "-" * 200,
        "NOSSO NUMERO          NUM.DOCUMENTO      NOME DO SACADO                              VALOR DO TITULO"
        "    DT.VENCTO     SITUACAO                                 ULTIMO COMANDO    DT.ENTRADA",
        "-" * 200,
    ]
    for t in titulos:
        parcela = f"/{t.parcela:03d}/{t.total:03d}" if t.parcela else ""
        linhas.append(
            f" {t.nosso_numero}    {t.documento:<7}{parcela:<12}{t.nome[:30]:<30}{_brl(t.valor):>29}"
            f"    {t.vencimento:%d.%m.%Y}    {t.situacao:<41}{t.ultimo_comando:%d.%m.%Y}        {t.entrada:%d.%m.%Y}"
        )
        linhas.append("")
    return "\r\n".join(linhas) + "\r\n"


def gerar_demo(hoje: date, quantidade: int = 18, semente: int = 2026):
    rng = random.Random(semente)
    clientes = gerar_clientes(rng, quantidade)
    titulos = gerar_titulos(hoje, clientes, rng)
    return clientes, titulos, rng
