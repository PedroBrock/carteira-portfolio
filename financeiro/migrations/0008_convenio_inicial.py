"""Convenio de cobranca da construtora (agencia e beneficiario de exemplo, ajustados pelo admin) e permissoes das remessas."""

from django.contrib.auth.management import create_permissions
from django.db import migrations


def criar(apps, schema_editor):
    ConvenioCaixa = apps.get_model("financeiro", "ConvenioCaixa")
    if not ConvenioCaixa.objects.exists():
        ConvenioCaixa.objects.create(agencia="0123", codigo_beneficiario="7654321")

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    perms = Permission.objects.filter(
        content_type__app_label="financeiro", codename__in=["view_remessacaixa", "view_conveniocaixa"]
    )
    for nome in ["Financeiro", "Consulta"]:
        grupo = Group.objects.filter(name=nome).first()
        if grupo:
            grupo.permissions.add(*perms)


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0007_convenio_e_remessa_caixa")]

    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
