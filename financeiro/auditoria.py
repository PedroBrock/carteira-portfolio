"""Registro de atividades dos usuarios (acessos e importacoes)."""

from .models import Atividade


def ip_do_request(request):
    """IP de quem acessou. Atras do proxy da hospedagem o IP real vem nos cabecalhos X-Real-IP/X-Forwarded-For."""
    if request is None:
        return None
    ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    ip = ip or request.META.get("REMOTE_ADDR")
    return ip or None


def registrar(request, tipo, descricao="", usuario=None, usuario_nome=""):
    if usuario is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        usuario = request.user
    return Atividade.objects.create(
        usuario=usuario,
        usuario_nome=usuario_nome or (usuario.get_username() if usuario else ""),
        tipo=tipo,
        descricao=descricao,
        ip=ip_do_request(request),
    )
