from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Carteira - Financeiro"
admin.site.site_title = "Carteira"
admin.site.index_title = "Financiamentos dos clientes"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("financeiro.urls")),
]
