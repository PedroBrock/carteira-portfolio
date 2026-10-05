from datetime import date

from django.test import SimpleTestCase

from financeiro.regras import Situacao, calcular_situacao, um_mes_depois


class UmMesDepoisTests(SimpleTestCase):
    def test_mesma_data_no_mes_seguinte(self):
        self.assertEqual(um_mes_depois(date(2026, 8, 30)), date(2026, 9, 30))

    def test_virada_de_ano(self):
        self.assertEqual(um_mes_depois(date(2026, 12, 20)), date(2027, 1, 20))

    def test_dia_inexistente_usa_ultimo_dia_do_mes(self):
        self.assertEqual(um_mes_depois(date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(um_mes_depois(date(2028, 1, 30)), date(2028, 2, 29))
        self.assertEqual(um_mes_depois(date(2026, 3, 31)), date(2026, 4, 30))


class CalcularSituacaoTests(SimpleTestCase):
    def situacao(self, vencimento, hoje, pago=None, verificar=False):
        return calcular_situacao(vencimento=vencimento, data_pagamento=pago, requer_verificacao=verificar, hoje=hoje)

    def test_pago(self):
        self.assertEqual(self.situacao(date(2025, 1, 10), date(2026, 9, 29), pago=date(2025, 1, 9)), Situacao.PAGO)

    def test_no_dia_do_vencimento_ainda_nao_venceu(self):
        self.assertEqual(self.situacao(date(2026, 9, 29), date(2026, 9, 29)), Situacao.A_VENCER)

    def test_menos_de_um_mes_apos_vencimento_ainda_a_vencer(self):
        self.assertEqual(self.situacao(date(2026, 8, 30), date(2026, 9, 29)), Situacao.A_VENCER)

    def test_no_dia_que_completa_um_mes_ainda_a_vencer(self):
        self.assertEqual(self.situacao(date(2026, 8, 29), date(2026, 9, 29)), Situacao.A_VENCER)

    def test_passou_um_mes_vencido(self):
        self.assertEqual(self.situacao(date(2026, 8, 28), date(2026, 9, 29)), Situacao.VENCIDO)

    def test_verificar_tem_prioridade_sobre_data(self):
        self.assertEqual(self.situacao(date(2027, 4, 18), date(2026, 9, 29), verificar=True), Situacao.VERIFICAR)

    def test_pago_tem_prioridade_sobre_verificar(self):
        self.assertEqual(
            self.situacao(date(2027, 4, 18), date(2026, 9, 29), pago=date(2026, 9, 1), verificar=True), Situacao.PAGO
        )
