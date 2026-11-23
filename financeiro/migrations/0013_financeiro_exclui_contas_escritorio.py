"""Grupo Financeiro passa a poder excluir contas do escritorio (as demais exclusoes seguem so para superusuario)."""

from django.contrib.auth.management import create_permissions
from django.db import migrations


def dar_permissao(apps, schema_editor):
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    grupo = Group.objects.filter(name="Financeiro").first()
    if grupo:
        grupo.permissions.add(
            *Permission.objects.filter(content_type__app_label="financeiro", codename="delete_contaescritorio")
        )


def tirar_permissao(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    grupo = Group.objects.filter(name="Financeiro").first()
    if grupo:
        grupo.permissions.remove(
            *Permission.objects.filter(content_type__app_label="financeiro", codename="delete_contaescritorio")
        )


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0012_conta_escritorio_parcelas")]

    operations = [migrations.RunPython(dar_permissao, tirar_permissao)]
