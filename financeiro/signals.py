from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from . import pendencias
from .auditoria import registrar
from .models import Atividade, Cliente, Parcela, TituloExcluido


@receiver(post_delete, sender=Parcela)
def lembrar_titulo_excluido(sender, instance, **kwargs):
    """Ao excluir uma parcela que veio do banco, guarda o nosso numero para a
    importacao nao recria-la. Excluir o registro de TituloExcluido libera o titulo de novo."""
    if not instance.nosso_numero:
        return
    fin = instance.financiamento
    TituloExcluido.objects.get_or_create(
        nosso_numero=instance.nosso_numero,
        defaults={
            "cliente": fin.cliente.nome,
            "descricao": f"Parcela {instance.rotulo_numero} - venc. {instance.vencimento:%d/%m/%Y} - R$ {instance.valor}",
        },
    )


# ---------------------------------------------------------------- Acessos

@receiver(user_logged_in)
def registrar_login(sender, request, user, **kwargs):
    registrar(request, Atividade.Tipo.LOGIN, usuario=user)


@receiver(user_logged_out)
def registrar_logout(sender, request, user, **kwargs):
    if user is not None:
        registrar(request, Atividade.Tipo.LOGOUT, usuario=user)


@receiver(user_login_failed)
def registrar_login_falhou(sender, credentials, request=None, **kwargs):
    nome = credentials.get("username", "")
    registrar(request, Atividade.Tipo.LOGIN_FALHOU, f"Tentativa com o usuário '{nome}'", usuario_nome=nome)


# ---------------------------------------------------------------- Pendencias para a Caixa


@receiver(pre_save, sender=Parcela)
def guardar_parcela_antes(sender, instance, **kwargs):
    antes = Parcela.objects.filter(pk=instance.pk).values("vencimento", "valor", "data_pagamento").first()
    instance._antes = antes if instance.pk else None


@receiver(post_save, sender=Parcela)
def parcela_salva(sender, instance, created, **kwargs):
    if not created:
        pendencias.parcela_alterada(instance, instance._antes)


@receiver(post_delete, sender=Parcela)
def parcela_excluida(sender, instance, **kwargs):
    pendencias.parcela_excluida(instance)


@receiver(pre_save, sender=Cliente)
def guardar_cliente_antes(sender, instance, **kwargs):
    antigo = Cliente.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._antes = (antigo.dados_pagador(), antigo.cadastro_completo) if antigo else None


@receiver(post_save, sender=Cliente)
def cliente_salvo(sender, instance, created, **kwargs):
    if not created and instance._antes:
        pendencias.cliente_alterado(instance, *instance._antes)
