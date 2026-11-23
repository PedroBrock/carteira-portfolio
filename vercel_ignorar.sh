#!/bin/sh
# "Ignored Build Step" da Vercel (ignoreCommand no vercel.json): sair com 1 constroi o deploy, sair com 0 pula.
# So o main (producao) e construido. Olha o ambiente e tambem o branch, porque o VERCEL_ENV pode chegar vazio
# nesse passo: com so a checagem do ambiente, todo merge no main era pulado ("Canceled by Ignored Build Step").
echo "Ambiente: ${VERCEL_ENV:-(vazio)} | branch: ${VERCEL_GIT_COMMIT_REF:-(vazio)}"
if [ "$VERCEL_ENV" = "production" ] || [ "$VERCEL_GIT_COMMIT_REF" = "main" ]; then
    echo "Deploy de producao: construindo."
    exit 1
fi
echo "Nao e o main: deploy pulado."
exit 0
