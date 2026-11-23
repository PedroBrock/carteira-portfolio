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


class SettingsVercelTests(SimpleTestCase):
    def tearDown(self):
        carregar_settings()

    def test_na_vercel_libera_os_dominios_do_projeto(self):
        s = carregar_settings(
            VERCEL="1",
            VERCEL_PROJECT_PRODUCTION_URL="carteira.vercel.app",
            VERCEL_URL="carteira-abc123.vercel.app",
            VERCEL_BRANCH_URL="carteira-git-main.vercel.app",
        )
        for dominio in ("carteira.vercel.app", "carteira-abc123.vercel.app", "carteira-git-main.vercel.app"):
            self.assertIn(dominio, s.ALLOWED_HOSTS)
            self.assertIn(f"https://{dominio}", s.CSRF_TRUSTED_ORIGINS)
        self.assertNotIn("healthcheck.railway.app", s.ALLOWED_HOSTS)

    def test_na_vercel_desliga_cursor_do_servidor_por_causa_do_pooler(self):
        s = carregar_settings(VERCEL="1", DATABASE_URL="postgres://u:s@ep-x-pooler.sa-east-1.aws.neon.tech/neondb")
        self.assertTrue(s.DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"])

    def test_fora_da_vercel_nao_libera_dominio_vercel(self):
        s = carregar_settings(VERCEL_URL="carteira-abc123.vercel.app")
        self.assertNotIn("carteira-abc123.vercel.app", s.ALLOWED_HOSTS)
        self.assertFalse(s.DATABASES["default"].get("DISABLE_SERVER_SIDE_CURSORS"))
