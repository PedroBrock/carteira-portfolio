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


# Menu lateral, em grupos: (grupo, [(rotulo, rota, permissao exigida, icone)]).
# "superusuario" = so superusuario; None = qualquer membro da equipe. O icone e uma classe do tema.css.
MENU_PRINCIPAL = [
    (None, [
        ("Painel", "financeiro:painel", None, "painel"),
    ]),
    ("Carteira", [
        ("Parcelas", "admin:financeiro_parcela_changelist", "financeiro.view_parcela", "parcelas"),
        ("Financiamentos", "admin:financeiro_financiamento_changelist", "financeiro.view_financiamento", "financiamentos"),
        ("Clientes", "admin:financeiro_cliente_changelist", "financeiro.view_cliente", "clientes"),
    ]),
    ("Caixa", [
        ("Pendências Caixa", "admin:financeiro_pendenciacaixa_changelist", "financeiro.view_pendenciacaixa", "pendencias"),
        ("Importar extrato", "financeiro:importar_extrato", "financeiro.change_parcela", "importar"),
    ]),
    ("Escritório", [
        ("Contas do escritório", "admin:financeiro_contaescritorio_changelist", "financeiro.view_contaescritorio", "contas"),
        ("Relatórios", "financeiro:relatorios", None, "relatorios"),
    ]),
    ("Sistema", [
        ("Atividades", "financeiro:atividades", "superusuario", "atividades"),
        ("Todos os cadastros", "admin:index", None, "cadastros"),
    ]),
]


@register.inclusion_tag("financeiro/_menu.html", takes_context=True)
def menu_principal(context):
    """Itens do menu lateral que o usuario pode ver, em grupos; marca o da pagina atual."""
    from django.urls import reverse

    from financeiro.models import PendenciaCaixa

    request = context["request"]
    usuario = request.user
    grupos, itens = [], []
    for grupo, entradas in MENU_PRINCIPAL:
        visiveis = []
        for rotulo, rota, permissao, icone in entradas:
            if permissao == "superusuario" and not usuario.is_superuser:
                continue
            if permissao and permissao != "superusuario" and not usuario.has_perm(permissao):
                continue
            visiveis.append({"rotulo": rotulo, "url": reverse(rota), "icone": icone})
        if visiveis:
            grupos.append({"nome": grupo, "itens": visiveis})
            itens += visiveis
    # Item atual: o de endereco mais longo que comeca o caminho da pagina ("/" e "/admin/" so quando exatos).
    candidatos = [i for i in itens if request.path == i["url"] or (i["url"].count("/") > 2 and request.path.startswith(i["url"]))]
    if not candidatos and request.path.startswith(reverse("admin:index")):
        candidatos = [i for i in itens if i["url"] == reverse("admin:index")]
    if candidatos:
        max(candidatos, key=lambda i: len(i["url"]))["atual"] = True
    # Contador de pendencias abertas ao lado de "Pendencias Caixa"
    for item in itens:
        if item["icone"] == "pendencias":
            item["qtd"] = PendenciaCaixa.objects.filter(status=PendenciaCaixa.Status.PENDENTE).count()
    return {"grupos": grupos}
