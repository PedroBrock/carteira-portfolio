from django.contrib import admin, messages
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .models import Cliente, ConvenioCaixa, Financiamento, Parcela, PendenciaCaixa, RemessaCaixa, TituloExcluido
from .remessa import ConvenioIncompleto, gerar_remessa
from .regras import Situacao
from .templatetags.financeiro_tags import brl, selo_situacao


class SituacaoFilter(admin.SimpleListFilter):
    title = "situação"
    parameter_name = "situacao"

    def lookups(self, request, model_admin):
        return Situacao.choices

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(situacao=self.value())
        return queryset


# ---------------------------------------------------------------- Parcelas


class ParcelaInline(admin.TabularInline):
    model = Parcela
    extra = 0
    fields = ["numero", "valor", "vencimento", "data_pagamento", "requer_verificacao", "situacao_display", "nosso_numero"]
    readonly_fields = ["situacao_display"]
    ordering = ["vencimento", "numero"]

    @admin.display(description="situação")
    def situacao_display(self, obj):
        return selo_situacao(obj.calcular_situacao()) if obj.pk else "-"


@admin.register(Parcela)
class ParcelaAdmin(admin.ModelAdmin):
    list_display = ["cliente", "parcela", "valor_brl", "vencimento", "situacao_display", "data_pagamento", "nosso_numero"]
    list_filter = [SituacaoFilter, "vencimento"]
    search_fields = ["financiamento__cliente__nome", "nosso_numero"]
    date_hierarchy = "vencimento"
    list_select_related = ["financiamento__cliente"]
    autocomplete_fields = ["financiamento"]
    readonly_fields = ["data_limite", "situacao_banco", "ultimo_comando_banco"]
    actions = ["marcar_pago_hoje", "concluir_verificacao"]
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).com_situacao()

    @admin.display(description="cliente", ordering="financiamento__cliente__nome")
    def cliente(self, obj):
        return obj.financiamento.cliente

    @admin.display(description="parcela", ordering="numero")
    def parcela(self, obj):
        return obj.rotulo_numero

    @admin.display(description="valor", ordering="valor")
    def valor_brl(self, obj):
        return brl(obj.valor)

    @admin.display(description="situação", ordering="situacao")
    def situacao_display(self, obj):
        return selo_situacao(obj.situacao)

    @admin.action(description="Marcar como pago hoje")
    def marcar_pago_hoje(self, request, queryset):
        hoje = timezone.localdate()
        parcelas = list(queryset.filter(data_pagamento__isnull=True))
        for p in parcelas:
            p.data_pagamento = hoje
            p.save(update_fields=["data_pagamento"])  # save (e nao update) para gerar o pedido de baixa na Caixa
            self.log_change(request, p, f"Marcada como paga em {hoje:%d/%m/%Y} (ação em massa).")
        self.message_user(request, f"{len(parcelas)} parcela(s) marcada(s) como paga(s).", messages.SUCCESS)

    @admin.action(description="Concluir verificação (tirar de 'Baixado - verificar')")
    def concluir_verificacao(self, request, queryset):
        parcelas = list(queryset.filter(requer_verificacao=True))
        Parcela.objects.filter(pk__in=[p.pk for p in parcelas]).update(requer_verificacao=False)
        for p in parcelas:
            self.log_change(request, p, "Verificação concluída (ação em massa).")
        self.message_user(request, f"{len(parcelas)} parcela(s) saíram de verificação.", messages.SUCCESS)


# ---------------------------------------------------------------- Financiamentos


@admin.register(Financiamento)
class FinanciamentoAdmin(admin.ModelAdmin):
    list_display = ["__str__", "data_entrada", "pagas", "valor_total", "valor_pago", "valor_vencido"]
    search_fields = ["cliente__nome", "descricao"]
    list_select_related = ["cliente"]
    autocomplete_fields = ["cliente"]
    inlines = [ParcelaInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _qtd=Count("parcelas"),
                _qtd_pagas=Count("parcelas", filter=Q(parcelas__data_pagamento__isnull=False)),
                _total=Sum("parcelas__valor"),
                _pago=Sum("parcelas__valor", filter=Q(parcelas__data_pagamento__isnull=False)),
                _vencido=Sum(
                    "parcelas__valor",
                    filter=Q(
                        parcelas__data_pagamento__isnull=True,
                        parcelas__requer_verificacao=False,
                        parcelas__data_limite__lt=timezone.localdate(),
                    ),
                ),
            )
        )

    @admin.display(description="pagas")
    def pagas(self, obj):
        return f"{obj._qtd_pagas}/{obj._qtd}"

    @admin.display(description="valor total", ordering="_total")
    def valor_total(self, obj):
        return brl(obj._total)

    @admin.display(description="pago", ordering="_pago")
    def valor_pago(self, obj):
        return brl(obj._pago)

    @admin.display(description="vencido", ordering="_vencido")
    def valor_vencido(self, obj):
        return brl(obj._vencido)


# ---------------------------------------------------------------- Clientes


class FinanciamentoInline(admin.TabularInline):
    model = Financiamento
    extra = 0
    fields = ["descricao", "numero_documento", "total_parcelas", "data_entrada"]
    show_change_link = True


class ComVencidoFilter(admin.SimpleListFilter):
    title = "parcelas vencidas"
    parameter_name = "vencido"

    def lookups(self, request, model_admin):
        return [("sim", "Com parcelas vencidas"), ("nao", "Em dia")]

    def queryset(self, request, queryset):
        if self.value() == "sim":
            return queryset.filter(_vencido__gt=0)
        if self.value() == "nao":
            return queryset.filter(Q(_vencido__isnull=True) | Q(_vencido=0))
        return queryset


class CadastroBoletoFilter(admin.SimpleListFilter):
    title = "cadastro para boleto"
    parameter_name = "cadastro"

    def lookups(self, request, model_admin):
        return [("completo", "Completo"), ("incompleto", "Incompleto")]

    def queryset(self, request, queryset):
        vazio = Q()
        for campo in Cliente.CAMPOS_PAGADOR:
            vazio |= Q(**{campo: ""})
        if self.value() == "completo":
            return queryset.exclude(vazio)
        if self.value() == "incompleto":
            return queryset.filter(vazio)
        return queryset


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = [
        "nome", "cpf_cnpj", "telefone", "cadastro_boleto", "financiamentos_qtd", "valor_a_vencer", "valor_vencido"
    ]
    list_filter = [ComVencidoFilter, CadastroBoletoFilter]
    search_fields = ["nome", "cpf_cnpj", "email"]
    inlines = [FinanciamentoInline]
    fieldsets = [
        (
            "Dados do pagador (vão no boleto)",
            {
                "fields": ["nome", "cpf_cnpj", "endereco", "bairro", ("cidade", "uf"), "cep"],
                "description": "A Caixa exige todos estes campos para registrar o boleto. "
                "Depois do cadastro completo, alterar algum deles gera uma pendência para atualizar os boletos em aberto na Caixa.",
            },
        ),
        ("Contato", {"fields": ["telefone", "email"]}),
        (None, {"fields": ["observacoes"]}),
    ]

    @admin.display(description="cadastro p/ boleto", boolean=True)
    def cadastro_boleto(self, obj):
        return obj.cadastro_completo

    def get_queryset(self, request):
        hoje = timezone.localdate()
        nao_pago = Q(financiamentos__parcelas__data_pagamento__isnull=True, financiamentos__parcelas__requer_verificacao=False)
        return (
            super()
            .get_queryset(request)
            .annotate(
                _fin=Count("financiamentos", distinct=True),
                _a_vencer=Sum(
                    "financiamentos__parcelas__valor", filter=nao_pago & Q(financiamentos__parcelas__data_limite__gte=hoje)
                ),
                _vencido=Sum(
                    "financiamentos__parcelas__valor", filter=nao_pago & Q(financiamentos__parcelas__data_limite__lt=hoje)
                ),
            )
        )

    @admin.display(description="financiamentos", ordering="_fin")
    def financiamentos_qtd(self, obj):
        return obj._fin

    @admin.display(description="a vencer", ordering="_a_vencer")
    def valor_a_vencer(self, obj):
        return brl(obj._a_vencer)

    @admin.display(description="vencido", ordering="_vencido")
    def valor_vencido(self, obj):
        return brl(obj._vencido)


# ---------------------------------------------------------------- Titulos excluidos


@admin.register(TituloExcluido)
class TituloExcluidoAdmin(admin.ModelAdmin):
    """Parcelas excluidas que a importacao do extrato ignora.
    Excluir um registro daqui faz a parcela voltar na proxima importacao."""

    list_display = ["nosso_numero", "cliente", "descricao", "excluido_em"]
    search_fields = ["nosso_numero", "cliente"]
    readonly_fields = ["nosso_numero", "cliente", "descricao", "excluido_em"]

    def has_add_permission(self, request):
        return False


# ---------------------------------------------------------------- Pendencias para a Caixa


def _fmt(campo, valor):
    if valor in (None, ""):
        return "(vazio)"
    if campo == "vencimento":
        a, m, d = valor.split("-")
        return f"{d}/{m}/{a}"
    if campo == "valor":
        return brl(valor)
    return str(valor)


ROTULOS_CAMPOS = {
    "nome": "Nome", "cpf_cnpj": "CPF/CNPJ", "endereco": "Endereço", "bairro": "Bairro",
    "cidade": "Cidade", "uf": "UF", "cep": "CEP", "vencimento": "Vencimento", "valor": "Valor",
}


class PendenciaStatusFilter(admin.SimpleListFilter):
    """Por padrao mostra so as pendentes."""

    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [*PendenciaCaixa.Status.choices, ("todas", "Todas")]

    def choices(self, changelist):
        valor = self.value() or PendenciaCaixa.Status.PENDENTE
        for lookup, titulo in self.lookup_choices:
            yield {
                "selected": valor == lookup,
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": titulo,
            }

    def queryset(self, request, queryset):
        valor = self.value() or PendenciaCaixa.Status.PENDENTE
        return queryset if valor == "todas" else queryset.filter(status=valor)


@admin.register(PendenciaCaixa)
class PendenciaCaixaAdmin(admin.ModelAdmin):
    list_display = ["criada", "cliente", "parcela_info", "tipo", "o_que_mudar", "status", "criado_por"]
    list_filter = [PendenciaStatusFilter, "tipo"]
    search_fields = ["cliente", "nosso_numero"]
    list_select_related = ["criado_por"]
    actions = ["gerar_arquivo_remessa", "marcar_enviada", "cancelar"]
    readonly_fields = [
        "cliente", "descricao_parcela", "nosso_numero", "tipo", "o_que_mudar", "motivo", "status",
        "criado_em", "criado_por", "enviado_em", "enviado_por", "remessa", "parcela",
    ]
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="criada em", ordering="criado_em")
    def criada(self, obj):
        return timezone.localtime(obj.criado_em).strftime("%d/%m/%Y %H:%M")

    @admin.display(description="parcela")
    def parcela_info(self, obj):
        return format_html("{}<br><small>nosso nº {}</small>", obj.descricao_parcela, obj.nosso_numero)

    @admin.display(description="o que mudar na Caixa")
    def o_que_mudar(self, obj):
        if obj.tipo == PendenciaCaixa.Tipo.BAIXA:
            return format_html("<strong>Baixar o boleto</strong><br><small>{}</small>", obj.motivo)
        linhas = [
            format_html("{}: {} → <strong>{}</strong>", ROTULOS_CAMPOS.get(c, c), _fmt(c, obj.antes.get(c)), _fmt(c, v))
            for c, v in obj.depois.items()
            if obj.antes.get(c) != v
        ]
        if obj.motivo:
            linhas.append(format_html("<small>{}</small>", obj.motivo))
        return format_html_join(mark_safe("<br>"), "{}", ((linha,) for linha in linhas))

    @admin.action(description="Gerar arquivo de remessa para a Caixa (CNAB 240)", permissions=["change"])
    def gerar_arquivo_remessa(self, request, queryset):
        try:
            remessa, ignoradas = gerar_remessa(queryset, request.user)
        except ConvenioIncompleto as e:
            url = reverse("admin:financeiro_conveniocaixa_changelist")
            self.message_user(request, format_html('{} <a href="{}">Abrir convênio</a>', e, url), messages.ERROR)
            return None
        for p, motivo in ignoradas:
            self.message_user(request, f"Fora da remessa: {p.cliente} ({p.nosso_numero}) - {motivo}.", messages.WARNING)
        if remessa is None:
            self.message_user(request, "Nenhuma pendência selecionada pode ir por remessa.", messages.WARNING)
            return None
        for p in remessa.pendencias.all():
            self.log_change(request, p, f"Incluída na remessa {remessa.nsa}.")
        if remessa.teste:
            aviso = "Remessa de TESTE: as pendências continuam pendentes até a Caixa liberar a produção."
        else:
            aviso = "Pendências marcadas como enviadas. Envie o arquivo no e-Cobrança."
        self.message_user(request, f"Remessa {remessa.nsa} com {remessa.quantidade} título(s). {aviso}", messages.SUCCESS)
        return baixar(remessa)

    @admin.action(description="Marcar como feita/enviada à Caixa", permissions=["change"])
    def marcar_enviada(self, request, queryset):
        pendentes = list(queryset.filter(status=PendenciaCaixa.Status.PENDENTE))
        agora = timezone.now()
        for p in pendentes:
            p.status, p.enviado_em, p.enviado_por = PendenciaCaixa.Status.ENVIADA, agora, request.user
            p.save()
            self.log_change(request, p, "Marcada como enviada à Caixa.")
        self.message_user(request, f"{len(pendentes)} pendência(s) marcada(s) como enviada(s).", messages.SUCCESS)

    @admin.action(description="Cancelar (não fazer na Caixa)", permissions=["change"])
    def cancelar(self, request, queryset):
        pendentes = list(queryset.filter(status=PendenciaCaixa.Status.PENDENTE))
        for p in pendentes:
            p.status, p.motivo = PendenciaCaixa.Status.CANCELADA, f"Cancelada por {request.user.get_username()}."
            p.save()
            self.log_change(request, p, "Pendência cancelada.")
        self.message_user(request, f"{len(pendentes)} pendência(s) cancelada(s).", messages.SUCCESS)


def baixar(remessa):
    resposta = HttpResponse(remessa.conteudo.encode("latin-1"), content_type="text/plain; charset=latin-1")
    resposta["Content-Disposition"] = f'attachment; filename="{remessa.nome_arquivo}"'
    return resposta


@admin.register(RemessaCaixa)
class RemessaCaixaAdmin(admin.ModelAdmin):
    list_display = ["nsa", "gerada", "criado_por", "teste", "quantidade", "arquivo"]
    list_select_related = ["criado_por"]
    readonly_fields = ["nsa", "criado_em", "criado_por", "teste", "quantidade", "arquivo"]
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        baixar_url = path(
            "<int:pk>/baixar/", self.admin_site.admin_view(self.baixar_view), name="financeiro_remessacaixa_baixar"
        )
        return [baixar_url, *super().get_urls()]

    def baixar_view(self, request, pk):
        if not self.has_view_permission(request):
            return HttpResponse(status=403)
        return baixar(get_object_or_404(RemessaCaixa, pk=pk))

    @admin.display(description="gerada em", ordering="criado_em")
    def gerada(self, obj):
        return timezone.localtime(obj.criado_em).strftime("%d/%m/%Y %H:%M")

    @admin.display(description="arquivo")
    def arquivo(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:financeiro_remessacaixa_baixar", args=[obj.pk]), obj.nome_arquivo)


@admin.register(ConvenioCaixa)
class ConvenioCaixaAdmin(admin.ModelAdmin):
    list_display = ["__str__", "nome", "ambiente_teste"]

    def has_add_permission(self, request):
        return not ConvenioCaixa.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False
