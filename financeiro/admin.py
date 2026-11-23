from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.http import content_disposition_header
from django.utils.safestring import mark_safe

from .boleto import BoletoInvalido, boleto_da_parcela, clientes_que_completam, gerar_pdf, massa_de_teste, nome_arquivo
from .contas_pdf import PdfInvalido, ler_conta, texto_do_pdf
from .extrato import EM_ABERTO
from .models import Cliente, ContaEscritorio, ConvenioCaixa, Financiamento, Parcela, PendenciaCaixa, RemessaCaixa, TituloExcluido
from .remessa import ConvenioIncompleto, gerar_remessa
from .regras import Situacao, um_mes_depois
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
    actions = ["marcar_pago_hoje", "concluir_verificacao", "gerar_boletos"]
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

    @admin.action(description="Gerar boleto em PDF", permissions=["view"])
    def gerar_boletos(self, request, queryset):
        conv = convenio_para_boleto(self, request)
        if conv is None:
            return None
        hoje = timezone.localdate()
        boletos = []
        for p in queryset.select_related("financiamento__cliente"):
            try:
                boletos.append(boleto_da_parcela(p, conv, hoje))
            except BoletoInvalido as erro:
                self.message_user(request, f"Sem boleto: {p} - {erro}.", messages.WARNING)
        if not boletos:
            self.message_user(request, "Nenhuma parcela selecionada pode gerar boleto.", messages.WARNING)
            return None
        return resposta_pdf(boletos)


def convenio_para_boleto(model_admin, request):
    conv = ConvenioCaixa.objects.first()
    faltando = conv.faltando_boleto() if conv else ["todos os dados"]
    if faltando:
        model_admin.message_user(request, "Preencha no convênio Caixa: " + ", ".join(faltando) + ".", messages.ERROR)
        return None
    return conv


def resposta_pdf(boletos, nome=None):
    resposta = HttpResponse(gerar_pdf(boletos), content_type="application/pdf")
    resposta["Content-Disposition"] = content_disposition_header(True, nome or nome_arquivo(boletos))
    return resposta


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
    readonly_fields = ["massa_de_teste"]

    def get_urls(self):
        massa = path(
            "massa-de-teste/", self.admin_site.admin_view(self.massa_de_teste_view), name="financeiro_conveniocaixa_massa"
        )
        return [massa, *super().get_urls()]

    @admin.display(description="massa de teste de boletos")
    def massa_de_teste(self, obj):
        return format_html(
            '<a class="button" href="{}">Gerar massa de teste (PDF)</a><div class="help">De 10 a 20 boletos de '
            "parcelas em aberto, cobrindo todos os dígitos verificadores que a Caixa pede para homologar a "
            "emissão pelo sistema (manual 67.119, item 1.1).</div>",
            reverse("admin:financeiro_conveniocaixa_massa"),
        )

    def massa_de_teste_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        voltar = redirect("admin:financeiro_conveniocaixa_changelist")
        conv = convenio_para_boleto(self, request)
        if conv is None:
            return voltar
        hoje = timezone.localdate()
        parcelas = (
            Parcela.objects.filter(situacao_banco=EM_ABERTO, data_pagamento__isnull=True, vencimento__gte=hoje)
            .exclude(nosso_numero=None)
            .select_related("financiamento__cliente")
            .order_by("vencimento", "nosso_numero")
        )
        boletos, faltando = massa_de_teste(parcelas, conv, hoje)
        if faltando or len(boletos) < 10:
            sugeridos = clientes_que_completam(parcelas, conv, boletos)
            if sugeridos and faltando:
                dica = "Preencha o CPF/CNPJ destes clientes e tente de novo: " + "; ".join(c.nome for c in sugeridos) + "."
            else:
                dica = "Complete o CPF/CNPJ de mais clientes com parcelas em aberto e tente de novo."
            self.message_user(
                request,
                f"Não deu para montar a massa de teste: {len(boletos)} boleto(s) e faltam "
                + ", ".join(faltando or ["boletos"]) + ". " + dica,
                messages.ERROR,
            )
            return voltar
        return resposta_pdf(boletos, "massa_de_teste_caixa.pdf")

    def has_add_permission(self, request):
        return not ConvenioCaixa.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------- Contas do escritorio

# Reaproveita as cores dos selos das parcelas (static/financeiro/tema.css).
SELO_CONTA = {"PAGA": ("PAGO", "Paga"), "EM_ABERTO": ("A_VENCER", "Em aberto"), "ATRASADA": ("VENCIDO", "Atrasada")}


class ContaSituacaoFilter(admin.SimpleListFilter):
    title = "situação"
    parameter_name = "situacao"

    def lookups(self, request, model_admin):
        return [(chave, rotulo) for chave, (_, rotulo) in SELO_CONTA.items()]

    def queryset(self, request, queryset):
        hoje = timezone.localdate()
        if self.value() == "PAGA":
            return queryset.filter(data_pagamento__isnull=False)
        if self.value() == "EM_ABERTO":
            return queryset.filter(data_pagamento__isnull=True, vencimento__gte=hoje)
        if self.value() == "ATRASADA":
            return queryset.filter(data_pagamento__isnull=True, vencimento__lt=hoje)
        return queryset


class ContaForm(forms.ModelForm):
    """Cadastro de conta; com PDF, os campos deixados em branco sao preenchidos com o que for lido da fatura."""

    OBRIGATORIOS = ["descricao", "valor", "vencimento"]
    PREENCHIDOS_PELO_PDF = [*OBRIGATORIOS, "fornecedor", "linha_digitavel"]

    arquivo_pdf = forms.FileField(
        label="Trocar o PDF",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        help_text="Fatura ou boleto em PDF (até 4 MB). Fica guardado junto com a conta.",
    )

    class Meta:
        model = ContaEscritorio
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pdf_lido = None  # (conteudo, nome, dados lidos)
        if self.instance.pk is None:
            for campo in self.OBRIGATORIOS:
                self.fields[campo].required = False

    def clean_arquivo_pdf(self):
        arquivo = self.cleaned_data.get("arquivo_pdf")
        if arquivo:
            conteudo = arquivo.read()
            try:
                texto = texto_do_pdf(conteudo)
            except PdfInvalido as erro:
                raise forms.ValidationError(str(erro))
            self.pdf_lido = (conteudo, arquivo.name[:150], ler_conta(texto, timezone.localdate()))
        return arquivo

    def clean(self):
        dados = super().clean()
        if self.pdf_lido:
            lidos = self.pdf_lido[2]
            for campo in self.PREENCHIDOS_PELO_PDF:
                if dados.get(campo) in (None, "") and campo in lidos and campo not in self.errors:
                    dados[campo] = lidos[campo]
            if self.instance.pk is None and dados.get("categoria") == ContaEscritorio.Categoria.OUTROS and "categoria" in lidos:
                dados["categoria"] = lidos["categoria"]
            if self.instance.pk is None and lidos.get("linha_digitavel"):
                repetida = ContaEscritorio.objects.filter(linha_digitavel=lidos["linha_digitavel"]).first()
                if repetida:
                    self.add_error("arquivo_pdf", f"Este boleto já está cadastrado: {repetida}.")
        for campo in self.OBRIGATORIOS:
            if dados.get(campo) in (None, "") and campo not in self.errors:
                texto = "Não encontrei no PDF; preencha à mão." if self.pdf_lido else "Este campo é obrigatório."
                self.add_error(campo, texto)
        return dados


class ContaNovaForm(ContaForm):
    quantidade_parcelas = forms.IntegerField(
        label="Parcelas",
        required=False,
        min_value=1,
        max_value=120,
        initial=1,
        help_text="Conta parcelada: informe quantas parcelas e o valor de cada uma. "
        "Elas são lançadas de uma vez, uma por mês, a partir do vencimento abaixo.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["arquivo_pdf"].label = "PDF da conta"
        self.fields["arquivo_pdf"].help_text = (
            "Envie a fatura ou boleto em PDF e deixe os campos abaixo em branco: o sistema preenche descrição, "
            "categoria, fornecedor, valor, vencimento e linha digitável. O que você digitar tem preferência. "
            "O PDF fica guardado junto com a conta."
        )


class VariosPdfsInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class VariosPdfsField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", VariosPdfsInput(attrs={"accept": "application/pdf"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(VariosPdfsField, self).clean(arquivo, initial) for arquivo in data]
        return [super().clean(data, initial)]


class EnviarPdfsForm(forms.Form):
    arquivos = VariosPdfsField(label="PDFs das contas", help_text="Pode selecionar vários arquivos de uma vez (até 4 MB somando todos).")


CAMPOS_CONTA = ["descricao", "categoria", "fornecedor", "valor", "vencimento", "data_pagamento", "linha_digitavel", "observacoes"]


@admin.register(ContaEscritorio)
class ContaEscritorioAdmin(admin.ModelAdmin):
    form = ContaForm
    list_display = ["conta", "categoria", "fornecedor", "valor_brl", "vencimento", "situacao_display", "data_pagamento", "pdf_link"]
    list_filter = [ContaSituacaoFilter, "categoria"]
    search_fields = ["descricao", "fornecedor", "observacoes", "linha_digitavel"]
    date_hierarchy = "vencimento"
    actions = ["marcar_paga_hoje", "lancar_proximo_mes"]
    list_per_page = 50
    readonly_fields = ["parcela", "total_parcelas", "pdf_atual"]

    def get_queryset(self, request):
        return super().get_queryset(request).defer("pdf")

    def get_fields(self, request, obj=None):
        if obj is None:
            # Conta nova: PDF primeiro (preenche o resto) e, em vez do numero da parcela, quantas parcelas lancar.
            return ["arquivo_pdf", *CAMPOS_CONTA[:4], "quantidade_parcelas", *CAMPOS_CONTA[4:]]
        parcela = ["parcela", "total_parcelas"] if obj.parcela else []
        return [*CAMPOS_CONTA, *parcela, "pdf_atual", "arquivo_pdf"]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ContaNovaForm
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        if form.pdf_lido:
            obj.pdf, obj.pdf_nome, lidos = form.pdf_lido
            if not change:
                encontrados = [rotulo for campo, rotulo in [("valor", "valor"), ("vencimento", "vencimento"),
                               ("fornecedor", "fornecedor")] if campo in lidos]
                texto = f"Lido do PDF: {', '.join(encontrados)}. Confira os dados da conta." if encontrados else (
                    "O PDF foi guardado, mas não tem texto para ler (talvez seja uma imagem escaneada).")
                self.message_user(request, texto, messages.INFO)
        super().save_model(request, obj, form, change)
        quantidade = form.cleaned_data.get("quantidade_parcelas") or 1
        if not change and quantidade > 1:
            demais = obj.lancar_demais_parcelas(quantidade)
            for conta in demais:
                self.log_addition(request, conta, f"Parcela lançada junto com \"{obj}\".")
            ultima = demais[-1].vencimento
            self.message_user(
                request, f"{quantidade} parcelas lançadas, com vencimentos até {ultima:%d/%m/%Y}.", messages.SUCCESS
            )

    def get_urls(self):
        urls = [
            path("<int:pk>/pdf/", self.admin_site.admin_view(self.pdf_view), name="financeiro_contaescritorio_pdf"),
            path(
                "enviar-pdfs/",
                self.admin_site.admin_view(self.enviar_pdfs_view),
                name="financeiro_contaescritorio_enviar_pdfs",
            ),
        ]
        return [*urls, *super().get_urls()]

    def pdf_view(self, request, pk):
        conta = get_object_or_404(ContaEscritorio, pk=pk)
        if not self.has_view_permission(request, conta):
            raise PermissionDenied
        if not conta.pdf:
            raise Http404("Esta conta não tem PDF.")
        resposta = HttpResponse(bytes(conta.pdf), content_type="application/pdf")
        resposta["Content-Disposition"] = content_disposition_header(False, conta.pdf_nome or "conta.pdf")
        return resposta

    def enviar_pdfs_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = EnviarPdfsForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            criadas, problemas = [], []
            for arquivo in form.cleaned_data["arquivos"]:
                conta, problema = self.criar_conta_do_pdf(request, arquivo)
                if conta:
                    criadas.append(conta)
                else:
                    problemas.append(f"{arquivo.name}: {problema}")
            if criadas:
                self.message_user(
                    request, f"{len(criadas)} conta(s) cadastrada(s) a partir dos PDFs. Confira os dados.", messages.SUCCESS
                )
            for problema in problemas:
                self.message_user(request, problema, messages.WARNING)
            return redirect("admin:financeiro_contaescritorio_changelist")
        contexto = {
            **self.admin_site.each_context(request),
            "title": "Enviar PDFs de contas",
            "opts": self.model._meta,
            "form": form,
        }
        return render(request, "admin/financeiro/contaescritorio/enviar_pdfs.html", contexto)

    def criar_conta_do_pdf(self, request, arquivo):
        """Cadastra uma conta a partir do PDF; devolve (conta, None) ou (None, motivo)."""
        conteudo = arquivo.read()
        try:
            dados = ler_conta(texto_do_pdf(conteudo), timezone.localdate())
        except PdfInvalido as erro:
            return None, str(erro)
        if "valor" not in dados or "vencimento" not in dados:
            return None, "não encontrei o valor e o vencimento no PDF; cadastre à mão em Adicionar conta."
        if dados.get("linha_digitavel"):
            repetida = ContaEscritorio.objects.filter(linha_digitavel=dados["linha_digitavel"]).first()
            if repetida:
                return None, f"este boleto já está cadastrado ({repetida})."
        nome = arquivo.name[:150]
        conta = ContaEscritorio.objects.create(
            descricao=dados.get("descricao") or nome.rsplit(".", 1)[0][:150],
            categoria=dados.get("categoria", ContaEscritorio.Categoria.OUTROS),
            fornecedor=dados.get("fornecedor", ""),
            valor=dados["valor"],
            vencimento=dados["vencimento"],
            linha_digitavel=dados.get("linha_digitavel", ""),
            pdf=conteudo,
            pdf_nome=nome,
        )
        self.log_addition(request, conta, f"Cadastrada a partir do PDF \"{nome}\".")
        return conta, None

    def changelist_view(self, request, extra_context=None):
        resposta = super().changelist_view(request, extra_context)
        if hasattr(resposta, "context_data") and "cl" in resposta.context_data:
            # Totais do que esta filtrado na tela (ex.: um mes pelo calendario de vencimento).
            contas = resposta.context_data["cl"].queryset
            resposta.context_data["totais_contas"] = {
                "total": contas.aggregate(v=Sum("valor"))["v"],
                "pago": contas.filter(data_pagamento__isnull=False).aggregate(v=Sum("valor"))["v"],
                "em_aberto": contas.filter(data_pagamento__isnull=True).aggregate(v=Sum("valor"))["v"],
            }
        return resposta

    @admin.display(description="descrição", ordering="descricao")
    def conta(self, obj):
        return f"{obj.descricao}{obj.rotulo_parcela}"

    @admin.display(description="valor", ordering="valor")
    def valor_brl(self, obj):
        return brl(obj.valor)

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if not obj.pdf_nome:
            return "-"
        return format_html('<a href="{}" target="_blank">abrir</a>', reverse("admin:financeiro_contaescritorio_pdf", args=[obj.pk]))

    @admin.display(description="PDF guardado")
    def pdf_atual(self, obj):
        if not obj.pdf_nome:
            return "Nenhum"
        url = reverse("admin:financeiro_contaescritorio_pdf", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">{}</a>', url, obj.pdf_nome)

    @admin.display(description="situação")
    def situacao_display(self, obj):
        classe, rotulo = SELO_CONTA[obj.situacao()]
        return format_html('<span class="selo selo-{}">{}</span>', classe, rotulo)

    @admin.action(description="Marcar como paga hoje", permissions=["change"])
    def marcar_paga_hoje(self, request, queryset):
        hoje = timezone.localdate()
        contas = list(queryset.filter(data_pagamento__isnull=True))
        for conta in contas:
            conta.data_pagamento = hoje
            conta.save(update_fields=["data_pagamento"])
            self.log_change(request, conta, f"Marcada como paga em {hoje:%d/%m/%Y} (ação em massa).")
        self.message_user(request, f"{len(contas)} conta(s) marcada(s) como paga(s).", messages.SUCCESS)

    @admin.action(description="Lançar de novo no mês seguinte (mesmo valor, em aberto)", permissions=["add"])
    def lancar_proximo_mes(self, request, queryset):
        novas = []
        for conta in queryset:
            nova = ContaEscritorio.objects.create(
                descricao=conta.descricao,
                categoria=conta.categoria,
                fornecedor=conta.fornecedor,
                valor=conta.valor,
                vencimento=um_mes_depois(conta.vencimento),
            )
            self.log_addition(request, nova, f"Lançada a partir de \"{conta}\" (ação em massa).")
            novas.append(nova)
        self.message_user(
            request, f"{len(novas)} conta(s) lançada(s) no mês seguinte. Confira os valores quando chegarem.", messages.SUCCESS
        )
