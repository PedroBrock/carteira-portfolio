from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from financeiro.demo import cpf_ficticio, formatar_extrato, gerar_demo
from financeiro.extrato import ler_extrato
from financeiro.models import Parcela, PendenciaCaixa, RemessaCaixa
from financeiro.regras import Situacao
from financeiro.tests.test_importacao import HOJE
from financeiro.validadores import validar_cpf_cnpj


class DemoTests(TestCase):
    def popular(self, *args):
        call_command("popular_demo", *args, stdout=StringIO())

    def test_extrato_ficticio_e_lido_pelo_leitor_da_caixa(self):
        _, titulos, _ = gerar_demo(HOJE)
        lidos = ler_extrato(formatar_extrato(titulos))
        self.assertEqual([t.nosso_numero for t in lidos], [t.nosso_numero for t in titulos])
        self.assertEqual(lidos[0].valor, titulos[0].valor)

    def test_cpfs_ficticios_sao_validos(self):
        import random

        rng = random.Random(1)
        for _ in range(50):
            validar_cpf_cnpj(cpf_ficticio(rng))

    def test_popula_todas_as_situacoes_e_pendencias(self):
        self.popular()
        situacoes = set(Parcela.objects.com_situacao().values_list("situacao", flat=True))
        self.assertEqual(situacoes, set(Situacao.values))
        self.assertEqual(PendenciaCaixa.objects.filter(status=PendenciaCaixa.Status.PENDENTE).count(), 4)
        self.assertEqual(RemessaCaixa.objects.count(), 1)

    def test_nao_sobrescreve_sem_limpar(self):
        self.popular()
        with self.assertRaises(CommandError):
            self.popular()
        self.popular("--limpar")
        self.assertEqual(PendenciaCaixa.objects.count(), 4)
