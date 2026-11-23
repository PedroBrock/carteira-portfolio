#!/bin/sh
# Build na Vercel (depois de instalar as dependencias, antes de publicar): aplica as migracoes e cria
# o admin (se configurado), como o entrypoint.sh faz no Docker. So no deploy de producao: o vercel.json
# ignora os outros deploys, e esta checagem garante que nenhum deles mexa no banco de producao.
set -e

# Mesma regra do vercel_ignorar.sh: producao pelo ambiente ou pelo branch main (o VERCEL_ENV pode vir vazio).
if [ "$VERCEL_ENV" != "production" ] && [ "$VERCEL_GIT_COMMIT_REF" != "main" ]; then
    echo "Deploy de ${VERCEL_ENV:-(ambiente vazio)} no branch ${VERCEL_GIT_COMMIT_REF:-(vazio)}: migracoes nao aplicadas."
    exit 0
fi

if [ -z "$DATABASE_URL" ]; then
    echo "Falta o DATABASE_URL: conecte o banco Neon ao projeto (aba Storage) e faca o redeploy."
    exit 1
fi

# Migracao pela conexao direta do Neon; o site usa a do pooler (DATABASE_URL).
DATABASE_URL="${DATABASE_URL_UNPOOLED:-$DATABASE_URL}" python manage.py migrate --noinput

if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    DATABASE_URL="${DATABASE_URL_UNPOOLED:-$DATABASE_URL}" \
        python manage.py createsuperuser --noinput --email "${DJANGO_SUPERUSER_EMAIL:-admin@localhost}" 2>/dev/null \
        && echo "Usuario admin criado." || echo "Usuario admin ja existe."
fi
