from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from financeiro.extrato import ExtratoInvalido, ler_arquivo
from financeiro.importacao import importar_titulos
from financeiro.models import Parcela
from financeiro.regras import Situacao


class Command(BaseCommand):
    help = "Importa o relatório 'CONSULTA DE TITULOS' da Caixa (.txt) para o sistema."

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="Caminho do arquivo .txt exportado do banco")

    def handle(self, *args, arquivo, **options):
        try:
            titulos = ler_arquivo(arquivo)
        except (OSError, ExtratoInvalido) as e:
            raise CommandError(str(e)) from e
        if not titulos:
            raise CommandError("Nenhum título encontrado no arquivo.")

        hoje = timezone.localdate()
        stats = importar_titulos(titulos, hoje)
        self.stdout.write(self.style.SUCCESS(f"{len(titulos)} títulos lidos."))
        for chave in (
            "clientes_novos",
            "financiamentos_novos",
            "parcelas_novas",
            "parcelas_atualizadas",
            "titulos_excluidos_ignorados",
        ):
            self.stdout.write(f"  {chave.replace('_', ' ')}: {stats[chave]}")

        self.stdout.write("Situação atual das parcelas:")
        contagem = {s: 0 for s in Situacao.values}
        for linha in Parcela.objects.com_situacao(hoje).values_list("situacao", flat=True):
            contagem[linha] += 1
        for valor, rotulo in Situacao.choices:
            self.stdout.write(f"  {rotulo}: {contagem[valor]}")
