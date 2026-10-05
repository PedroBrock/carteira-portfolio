"""Regras de negocio da situacao das parcelas.

- PAGO: a parcela tem data de pagamento.
- A VENCER: nao paga e ainda nao passou 1 mes do vencimento
  (inclusive o proprio dia do vencimento e o dia em que completa 1 mes).
- VENCIDO: nao paga e ja passou 1 mes do vencimento.
- VERIFICAR: baixada pelo banco antes de vencer (ex.: cancelamento/renegociacao);
  precisa de analise manual.
"""

import calendar
from datetime import date

from django.db import models


class Situacao(models.TextChoices):
    PAGO = "PAGO", "Pago"
    A_VENCER = "A_VENCER", "A vencer"
    VENCIDO = "VENCIDO", "Vencido"
    VERIFICAR = "VERIFICAR", "Baixado - verificar"


def um_mes_depois(d: date) -> date:
    """Mesma data no mes seguinte; se o dia nao existir, usa o ultimo dia do mes."""
    ano, mes = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def calcular_situacao(*, vencimento: date, data_pagamento: date | None, requer_verificacao: bool, hoje: date) -> str:
    if data_pagamento:
        return Situacao.PAGO
    if requer_verificacao:
        return Situacao.VERIFICAR
    if hoje > um_mes_depois(vencimento):
        return Situacao.VENCIDO
    return Situacao.A_VENCER
