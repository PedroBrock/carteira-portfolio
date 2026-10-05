"""Validacao e formatacao de CPF, CNPJ e CEP (a Caixa rejeita boletos com documento invalido)."""

import re

from django.core.exceptions import ValidationError

UFS = (
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)


def so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _cpf_valido(d: str) -> bool:
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(d[i]) * (tam + 1 - i) for i in range(tam))
        if int(d[tam]) != (soma * 10 % 11) % 10:
            return False
    return True


def _cnpj_valido(d: str) -> bool:
    if len(d) != 14 or d == d[0] * 14:
        return False
    pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for tam in (12, 13):
        soma = sum(int(d[i]) * pesos[i + 13 - tam] for i in range(tam))
        resto = soma % 11
        if int(d[tam]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def formatar_cpf_cnpj(valor: str) -> str:
    d = so_digitos(valor)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return valor


def validar_cpf_cnpj(valor: str):
    d = so_digitos(valor)
    if len(d) == 11 and _cpf_valido(d):
        return
    if len(d) == 14 and _cnpj_valido(d):
        return
    raise ValidationError("CPF ou CNPJ inválido.")


def validar_cep(valor: str):
    if len(so_digitos(valor)) != 8:
        raise ValidationError("CEP deve ter 8 dígitos.")


def formatar_cep(valor: str) -> str:
    d = so_digitos(valor)
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else valor


def validar_uf(valor: str):
    if (valor or "").upper() not in UFS:
        raise ValidationError("UF inválida.")
