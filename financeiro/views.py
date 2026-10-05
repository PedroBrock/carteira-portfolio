import calendar
from datetime import date, timedelta

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .auditoria import registrar
from .extrato import ExtratoInvalido, ler_extrato
from .importacao import importar_titulos
from .models import Atividade, Parcela, PendenciaCaixa
from .regras import Situacao


@staff_member_required
def painel(request):
    hoje = timezone.localdate()
    parcelas = Parcela.objects.com_situacao(hoje).select_related("financiamento__cliente")

    totais = {s: {"qtd": 0, "total": 0} for s in Situacao.values}
    for linha in parcelas.values("situacao").annotate(qtd=Count("id"), total=Sum("valor")):
        totais[linha["situacao"]] = linha

    recebido_mes = Parcela.objects.filter(
        data_pagamento__year=hoje.year, data_pagamento__month=hoje.month
    ).aggregate(qtd=Count("id"), total=Sum("valor"))

    contexto = {
        **admin.site.each_context(request),
        "title": "Painel",
        "pendencias_caixa": PendenciaCaixa.objects.filter(status=PendenciaCaixa.Status.PENDENTE).count(),
        "hoje": hoje,
        "totais": totais,
        "recebido_mes": recebido_mes,
        "vencidas": parcelas.filter(situacao=Situacao.VENCIDO).order_by("vencimento"),
        "verificar": parcelas.filter(situacao=Situacao.VERIFICAR).order_by("vencimento"),
        "proximas": parcelas.filter(
            situacao=Situacao.A_VENCER, vencimento__gte=hoje, vencimento__lte=hoje + timedelta(days=30)
        ).order_by("vencimento"),
        "em_carencia": parcelas.filter(situacao=Situacao.A_VENCER, vencimento__lt=hoje).order_by("vencimento"),
    }
    return render(request, "financeiro/painel.html", contexto)


class ImportarExtratoForm(forms.Form):
    arquivo = forms.FileField(label="Arquivo .txt do extrato (CONSULTA DE TITULOS)")


@staff_member_required
def importar_extrato(request):
    if not request.user.has_perm("financeiro.change_parcela"):
        messages.error(request, "Você não tem permissão para importar extratos.")
        return redirect("financeiro:painel")

    form = ImportarExtratoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        texto = form.cleaned_data["arquivo"].read().decode("latin-1")
        try:
            titulos = ler_extrato(texto)
        except ExtratoInvalido as e:
            form.add_error("arquivo", str(e))
        else:
            if not titulos:
                form.add_error("arquivo", "Nenhum título encontrado no arquivo.")
            else:
                stats = importar_titulos(titulos, timezone.localdate())
                texto = (
                    f"{len(titulos)} títulos lidos: {stats['parcelas_novas']} parcelas novas, "
                    f"{stats['parcelas_atualizadas']} atualizadas, {stats['clientes_novos']} clientes novos."
                )
                if stats["titulos_excluidos_ignorados"]:
                    texto += f" {stats['titulos_excluidos_ignorados']} título(s) excluído(s) anteriormente foram ignorados."
                messages.success(request, texto)
                registrar(
                    request,
                    Atividade.Tipo.IMPORTACAO,
                    f"Arquivo '{form.cleaned_data['arquivo'].name}': {texto}",
                )
                return redirect("financeiro:painel")
    return render(request, "financeiro/importar.html", {**admin.site.each_context(request), "form": form, "title": "Importar extrato"})


@require_GET
def saude(request):
    """Usado pela hospedagem para saber se o sistema esta no ar."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


# ---------------------------------------------------------------- Registro de atividades

TIPOS_ATIVIDADE = [
    ("CRIOU", "Criou"),
    ("ALTEROU", "Alterou"),
    ("EXCLUIU", "Excluiu"),
    *Atividade.Tipo.choices,
]
_TIPO_DO_LOGENTRY = {ADDITION: "CRIOU", CHANGE: "ALTEROU", DELETION: "EXCLUIU"}
LIMITE_ATIVIDADES = 1000


class FiltroAtividadesForm(forms.Form):
    usuario = forms.ModelChoiceField(
        queryset=User.objects.order_by("username"), required=False, empty_label="Todos", label="Usuário"
    )
    tipo = forms.ChoiceField(choices=[("", "Todos"), *TIPOS_ATIVIDADE], required=False, label="Ação")
    de = forms.DateField(required=False, label="De", widget=forms.DateInput(attrs={"type": "date"}))
    ate = forms.DateField(required=False, label="Até", widget=forms.DateInput(attrs={"type": "date"}))


def _linhas_de_atividade(filtro):
    """Junta o historico do admin (LogEntry) com os acessos/importacoes (Atividade), mais recentes primeiro."""
    usuario, tipo, de, ate = (filtro.get(k) for k in ("usuario", "tipo", "de", "ate"))
    tipos_log = {v: k for k, v in _TIPO_DO_LOGENTRY.items()}

    logs = LogEntry.objects.select_related("user", "content_type")
    atividades = Atividade.objects.select_related("usuario")
    if usuario:
        logs = logs.filter(user=usuario)
        atividades = atividades.filter(usuario=usuario)
    if tipo:
        logs = logs.filter(action_flag=tipos_log[tipo]) if tipo in tipos_log else logs.none()
        atividades = atividades.filter(tipo=tipo) if tipo not in tipos_log else atividades.none()
    if de:
        logs = logs.filter(action_time__date__gte=de)
        atividades = atividades.filter(data__date__gte=de)
    if ate:
        logs = logs.filter(action_time__date__lte=ate)
        atividades = atividades.filter(data__date__lte=ate)

    linhas = [
        {
            "data": log.action_time,
            "usuario": log.user.get_username(),
            "tipo": _TIPO_DO_LOGENTRY[log.action_flag],
            "o_que": f"{log.content_type.name.capitalize()}: {log.object_repr}" if log.content_type else log.object_repr,
            "detalhes": log.get_change_message(),
            "link": None if log.is_deletion() else log.get_admin_url(),
            "ip": None,
        }
        for log in logs.order_by("-action_time")[:LIMITE_ATIVIDADES]
    ]
    linhas += [
        {
            "data": a.data,
            "usuario": a.usuario.get_username() if a.usuario else a.usuario_nome,
            "tipo": a.tipo,
            "o_que": "",
            "detalhes": a.descricao,
            "link": None,
            "ip": a.ip,
        }
        for a in atividades.order_by("-data")[:LIMITE_ATIVIDADES]
    ]
    rotulos = dict(TIPOS_ATIVIDADE)
    for linha in linhas:
        linha["tipo_rotulo"] = rotulos[linha["tipo"]]
    linhas.sort(key=lambda linha: linha["data"], reverse=True)
    return linhas[:LIMITE_ATIVIDADES]


@staff_member_required
def atividades(request):
    if not request.user.is_superuser:
        messages.error(request, "Só o administrador pode ver o registro de atividades.")
        return redirect("financeiro:painel")
    filtro = FiltroAtividadesForm(request.GET or None)
    linhas = _linhas_de_atividade(filtro.cleaned_data if filtro.is_valid() else {})
    pagina = Paginator(linhas, 50).get_page(request.GET.get("pagina"))
    params = request.GET.copy()
    params.pop("pagina", None)
    return render(
        request,
        "financeiro/atividades.html",
        {
            **admin.site.each_context(request),
            "title": "Registro de atividades",
            "filtro": filtro,
            "pagina": pagina,
            "params": params.urlencode(),
            "limite": LIMITE_ATIVIDADES,
        },
    )


# ---------------------------------------------------------------- Relatorios


def _mes(ano, mes):
    return date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])


def _somar(qs):
    r = qs.aggregate(qtd=Count("id"), total=Sum("valor"))
    return {"qtd": r["qtd"], "total": r["total"] or 0}


@staff_member_required
def relatorios(request):
    hoje = timezone.localdate()
    try:
        ano, mes = (int(x) for x in request.GET.get("mes", "").split("-"))
        inicio, fim = _mes(ano, mes)
    except ValueError:
        inicio, fim = _mes(hoje.year, hoje.month)
    prox_inicio, prox_fim = _mes(*((inicio.year + 1, 1) if inicio.month == 12 else (inicio.year, inicio.month + 1)))
    ant_inicio = (inicio - timedelta(days=1)).replace(day=1)

    # Parcelas em aberto: nao pagas e fora de "Baixado - verificar".
    em_aberto = Parcela.objects.filter(data_pagamento__isnull=True, requer_verificacao=False)
    do_mes = Parcela.objects.filter(vencimento__range=(inicio, fim))
    aberto_mes = em_aberto.filter(vencimento__range=(inicio, fim))

    pagas_mes = _somar(do_mes.filter(data_pagamento__isnull=False))
    a_receber_mes = _somar(aberto_mes)

    # Faixas iguais as do "Resumo Extrato Cobranca" da Caixa (contadas a partir de hoje).
    d30, d60 = hoje + timedelta(days=30), hoje + timedelta(days=60)
    carteira = {
        "em_carencia": _somar(em_aberto.filter(vencimento__lt=hoje, data_limite__gte=hoje)),
        "ate_30": _somar(em_aberto.filter(vencimento__gte=hoje, vencimento__lte=d30)),
        "ate_60": _somar(em_aberto.filter(vencimento__gt=d30, vencimento__lte=d60)),
        "apos_60": _somar(em_aberto.filter(vencimento__gt=d60)),
        "vencidas": _somar(em_aberto.filter(data_limite__lt=hoje)),
        "total_aberto": _somar(em_aberto),
    }

    contexto = {
        **admin.site.each_context(request),
        "title": "Relatórios",
        "hoje": hoje,
        "inicio": inicio,
        "prox_inicio": prox_inicio,
        "mes_anterior": f"{ant_inicio:%Y-%m}",
        "mes_seguinte": f"{prox_inicio:%Y-%m}",
        "mes": {
            "venceram": _somar(aberto_mes.filter(vencimento__lt=hoje)),
            "a_vencer": _somar(aberto_mes.filter(vencimento__gte=hoje)),
            "a_receber": a_receber_mes,
            "pagas": pagas_mes,
            "previsto": {
                "qtd": pagas_mes["qtd"] + a_receber_mes["qtd"],
                "total": pagas_mes["total"] + a_receber_mes["total"],
            },
        },
        "proximo_mes": _somar(em_aberto.filter(vencimento__range=(prox_inicio, prox_fim))),
        "carteira": carteira,
    }
    return render(request, "financeiro/relatorios.html", contexto)
