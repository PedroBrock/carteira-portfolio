"""Cria os grupos de acesso para funcionarios.

- Financeiro: consulta, cadastra e altera clientes, financiamentos e parcelas,
  e importa extratos. Nao exclui nada.
- Consulta: apenas visualiza.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

MODELOS = ["cliente", "financiamento", "parcela"]


def criar_grupos(apps, schema_editor):
    # Garante que as permissoes existam mesmo num banco novo (sao criadas so no fim do migrate).
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    def perms(acoes):
        codenames = [f"{acao}_{modelo}" for acao in acoes for modelo in MODELOS]
        return Permission.objects.filter(content_type__app_label="financeiro", codename__in=codenames)

    financeiro, _ = Group.objects.get_or_create(name="Financeiro")
    financeiro.permissions.set(perms(["view", "add", "change"]))
    consulta, _ = Group.objects.get_or_create(name="Consulta")
    consulta.permissions.set(perms(["view"]))


def remover_grupos(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=["Financeiro", "Consulta"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("financeiro", "0002_titulo_excluido"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(criar_grupos, remover_grupos)]
