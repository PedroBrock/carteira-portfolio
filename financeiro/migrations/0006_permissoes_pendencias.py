"""Grupos de acesso: Financeiro ve e trata as pendencias para a Caixa; Consulta so ve."""

from django.contrib.auth.management import create_permissions
from django.db import migrations


def dar_permissoes(apps, schema_editor):
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    def perms(*codenames):
        return Permission.objects.filter(content_type__app_label="financeiro", codename__in=codenames)

    for nome, codenames in [
        ("Financeiro", ["view_pendenciacaixa", "change_pendenciacaixa"]),
        ("Consulta", ["view_pendenciacaixa"]),
    ]:
        grupo = Group.objects.filter(name=nome).first()
        if grupo:
            grupo.permissions.add(*perms(*codenames))


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0005_cliente_bairro_pendencias_caixa")]

    operations = [migrations.RunPython(dar_permissoes, migrations.RunPython.noop)]
