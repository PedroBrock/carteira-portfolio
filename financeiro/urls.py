from django.urls import path

from . import views

app_name = "financeiro"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("importar/", views.importar_extrato, name="importar_extrato"),
    path("relatorios/", views.relatorios, name="relatorios"),
    path("atividades/", views.atividades, name="atividades"),
    path("saude/", views.saude, name="saude"),
]
