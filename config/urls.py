from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Carteira - Financeiro"
admin.site.site_title = "Carteira"
admin.site.index_title = "Financiamentos dos clientes"
# "Ver o site" levaria a "/", que e o mesmo Painel do menu; sem site_url o admin nao mostra o link.
admin.site.site_url = None
# O menu do topo (templates/admin/base_site.html) substitui a barra lateral de modelos do admin.
admin.site.enable_nav_sidebar = False

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("financeiro.urls")),
]
