import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


def carregar_settings(**env):
    base = {"DJANGO_DEBUG": "1"}
    with mock.patch.dict(os.environ, {**base, **env}, clear=True):
        import config.settings as s

        return importlib.reload(s)


class SettingsRailwayTests(SimpleTestCase):
    def tearDown(self):
        carregar_settings()

    def test_fora_do_railway_nao_libera_healthcheck(self):
        s = carregar_settings()
        self.assertNotIn("healthcheck.railway.app", s.ALLOWED_HOSTS)

    def test_no_railway_usa_dominio_publico_e_libera_healthcheck(self):
        s = carregar_settings(RAILWAY_ENVIRONMENT_NAME="production", RAILWAY_PUBLIC_DOMAIN="exemplo.up.railway.app")
        self.assertIn("healthcheck.railway.app", s.ALLOWED_HOSTS)
        self.assertIn("exemplo.up.railway.app", s.ALLOWED_HOSTS)
        self.assertIn("https://exemplo.up.railway.app", s.CSRF_TRUSTED_ORIGINS)

    def test_variaveis_manuais_continuam_valendo(self):
        s = carregar_settings(
            RAILWAY_ENVIRONMENT_NAME="production",
            DJANGO_ALLOWED_HOSTS="financeiro.construtora.com.br",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://financeiro.construtora.com.br",
        )
        self.assertIn("financeiro.construtora.com.br", s.ALLOWED_HOSTS)
        self.assertIn("https://financeiro.construtora.com.br", s.CSRF_TRUSTED_ORIGINS)
