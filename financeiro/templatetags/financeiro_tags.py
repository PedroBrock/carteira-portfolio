from decimal import Decimal

from django import template
from django.utils.html import format_html

from financeiro.regras import Situacao

register = template.Library()


def brl(valor) -> str:
    """Formata em reais: 1234.5 -> 'R$ 1.234,50'."""
    valor = Decimal(valor or 0)
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def selo_situacao(situacao: str):
    """Selo colorido da situacao; as cores (tema claro/escuro) ficam em static/financeiro/tema.css."""
    return format_html('<span class="selo selo-{}">{}</span>', situacao, Situacao(situacao).label)


register.filter("brl", brl)
register.filter("selo_situacao", selo_situacao)
