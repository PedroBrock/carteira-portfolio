# Colocar o sistema no ar

O sistema roda na **Vercel** (site) com o banco PostgreSQL no **Neon** (integração da própria Vercel), os dois
em São Paulo. A cada merge no `main` a Vercel instala as dependências, aplica as migrações do banco
(`vercel_build.sh`), junta os arquivos estáticos e publica. Os outros branches não geram deploy
(`ignoreCommand` no `vercel.json` roda o `vercel_ignorar.sh`, que olha o ambiente e o branch), para nenhum teste mexer no banco de produção.

Tudo o que o sistema guarda (parcelas, PDFs das contas, remessas para a Caixa, registro de atividades)
fica **no banco**. O site não grava nada em disco, então para mudar de hospedagem basta levar o banco e as variáveis.

## Limites da Vercel que afetam o sistema

- **Envio e resposta de até 4,5 MB.** Por isso o PDF de uma conta do escritório vai até 4 MB e, em
  "Enviar PDFs", os arquivos de cada envio somam no máximo 4 MB (o sistema avisa antes de enviar).
  Extrato da Caixa (~1 MB), arquivo de remessa e PDFs de boletos ficam bem abaixo.
- **5 minutos por requisição** (suficiente para importar extrato).
- **Plano:** o Hobby (grátis) da Vercel é só para uso pessoal, não comercial. Para o sistema da empresa use o
  **Pro (US$ 20/mês, já inclui US$ 20 de uso)**. O Neon grátis (1 GB, banco "dorme" após 5 min sem uso e acorda
  no primeiro acesso) atende; confira os valores atuais em vercel.com/pricing e neon.com/pricing.

## Primeira instalação na Vercel

1. Crie a conta em https://vercel.com entrando com o GitHub.
2. **Add New → Project →** importe o seu fork deste repositório. Na tela de importação abra **Environment Variables**
   e cadastre (troque os valores em MAIÚSCULAS):

   ```
   DJANGO_SECRET_KEY=COLE_AQUI_UMA_CHAVE_ALEATORIA_LONGA
   DJANGO_SUPERUSER_USERNAME=admin
   DJANGO_SUPERUSER_PASSWORD=ESCOLHA_UMA_SENHA_FORTE
   DJANGO_SUPERUSER_EMAIL=SEU_EMAIL
   ```

   `DJANGO_SECRET_KEY`: 50+ caracteres aleatórios (`python -c "import secrets; print(secrets.token_urlsafe(50))"`).
   Clique em **Deploy**. O primeiro deploy **falha** com "Falta o DATABASE_URL" — é normal, o banco vem no passo 4.
3. **Settings → Environments → Production → Branch Tracking**: confirme que o branch de produção é **`main`**
   (a Vercel usa o branch padrão do GitHub, que precisa ser o `main`).
4. Aba **Storage → Create Database → Neon (Serverless Postgres)**, região **São Paulo (sa-east-1)**, e conecte ao
   projeto. A Vercel cria sozinha as variáveis `DATABASE_URL` (conexão pelo pooler, usada pelo site) e
   `DATABASE_URL_UNPOOLED` (conexão direta, usada nas migrações). Não crie essas duas à mão.
5. **Deployments →** no último deploy, **⋯ → Redeploy**. Quando ficar "Ready", abra o endereço `.vercel.app`,
   entre com o usuário e a senha do passo 2 e depois apague a variável `DJANGO_SUPERUSER_PASSWORD`.

O endereço `.vercel.app` do projeto é liberado sozinho pelo sistema (não precisa de `DJANGO_ALLOWED_HOSTS`).

## Mudar do Railway para a Vercel

**Não apague nada no Railway até o site na Vercel estar conferido.** O Railway é a cópia de segurança durante a mudança.

### 1. Guardar o que está no Railway

- **Variáveis:** no Railway, card do sistema → **Variables → Raw Editor**, copie tudo para um arquivo
  guardado em lugar seguro (não no GitHub). Na Vercel só serão usadas `DJANGO_SECRET_KEY` (use a **mesma**) e,
  se tiver domínio próprio, `DJANGO_ALLOWED_HOSTS`. `DATABASE_URL`, `WEB_CONCURRENCY` e as `RAILWAY_*` não vão.
- **Banco (backup completo):** card do **Postgres → Settings → Networking**, ative o **TCP Proxy** (se ainda não
  estiver ativo) e copie a `DATABASE_PUBLIC_URL` da aba **Variables**. Com Docker instalado, numa pasta vazia:

  ```bash
  docker run --rm -v "$PWD:/backup" postgres:17 \
    pg_dump --format=custom --no-owner --no-acl --file=/backup/carteira_railway.dump "URL_PUBLICA_DO_RAILWAY"
  ```

  (Sem Docker: instale o cliente do PostgreSQL — no Windows, o instalador do postgresql.org com "Command Line Tools";
  no Mac, `brew install libpq` — e rode o mesmo `pg_dump` sem a parte do `docker run`.)
  Se aparecer "server version mismatch", troque `postgres:17` pela versão da mensagem.
  Guarde o arquivo `carteira_railway.dump`: ele é o backup de tudo (clientes, parcelas, PDFs, remessas, usuários).
- **A partir daqui não altere nada no site do Railway**, senão a mudança não vai para a Vercel.

### 2. Montar a Vercel com os dados

1. Siga a [primeira instalação](#primeira-instalação-na-vercel) até o passo 4 (crie o Neon na **mesma versão** do
   PostgreSQL do Railway ou mais nova), **sem** o redeploy do passo 5. Use a `DJANGO_SECRET_KEY` do Railway e não
   cadastre `DJANGO_SUPERUSER_*` (os usuários vêm do backup).
2. Copie a `DATABASE_URL_UNPOOLED` (Vercel → Settings → Environment Variables, ou Neon → **Connect** com
   "Connection pooling" desligado) e restaure o backup no banco novo, que ainda está vazio:

   ```bash
   docker run --rm -v "$PWD:/backup" postgres:17 \
     pg_restore --no-owner --no-acl --dbname="URL_DIRETA_DO_NEON" /backup/carteira_railway.dump
   ```

   Se terminar com "errors ignored on restore", leia as mensagens: aviso sobre o schema `public` já existir pode
   ser ignorado; qualquer outro erro, apague o banco no Neon, crie de novo e repita (o Railway continua intacto).
3. Faça o **Redeploy** (passo 5 da instalação). As migrações novas, se houver, são aplicadas no banco restaurado.

### 3. Conferir antes de desligar o Railway

Abra os dois sites lado a lado e confira:

- [ ] Login com o seu usuário e com o de um funcionário.
- [ ] Painel: totais a vencer / vencido / recebido no mês iguais aos do Railway.
- [ ] Parcelas, clientes e financiamentos: mesma quantidade nas listas.
- [ ] Contas do escritório: abrir um PDF guardado.
- [ ] Remessas para a Caixa: baixar uma remessa antiga.
- [ ] Gerar um boleto em PDF.
- [ ] Importar um extrato (pode ser o mesmo já importado: nada é duplicado).
- [ ] `https://SEU-PROJETO.vercel.app/saude/` responde "ok".

### 4. Domínio próprio (se usar)

1. Vercel → **Settings → Domains → Add**, digite `financeiro.suaconstrutora.com.br` e crie no provedor do domínio
   o registro que a Vercel mostrar (normalmente um **CNAME** para `cname.vercel-dns.com`), **no lugar** do CNAME que
   aponta para o Railway. No Registro.br: domínio → **DNS → Editar zona**. Na Cloudflare, nuvem **cinza** ("DNS only").
2. Variável na Vercel: `DJANGO_ALLOWED_HOSTS=financeiro.suaconstrutora.com.br` → Redeploy.
3. Quando a Vercel mostrar o domínio como válido, o HTTPS é emitido sozinho.

Se abrir com "Bad Request (400)", falta (ou está diferente) o `DJANGO_ALLOWED_HOSTS`.

### 5. Desligar o Railway

Use só a Vercel por **uma ou duas semanas**. Se tudo estiver certo, guarde o `carteira_railway.dump` e então
apague o projeto no Railway (e cancele o plano). Depois que a Vercel recebeu alterações, **não volte** para o
Railway: o que foi feito na Vercel não estaria lá.

## Usuários (funcionários)

Crie um usuário para cada pessoa (não compartilhe o admin):

1. Admin → **Usuários → Adicionar usuário**: nome de usuário e uma senha inicial → **Salvar e continuar editando**.
2. Na tela seguinte marque **Membro da equipe** (necessário para entrar). **Não** marque "Status de superusuário".
3. Em **Grupos**, escolha um:
   - **Financeiro**: vê, cadastra e altera clientes/financiamentos/parcelas e importa extratos. Não exclui nada.
   - **Consulta**: só visualiza.
4. Salve e envie ao funcionário o endereço do site, o usuário e a senha inicial (de preferência por canais
   diferentes); peça para ele trocar a senha em "Alterar senha" no primeiro acesso.

Para desligar alguém, desmarque **Ativo** no usuário (o histórico de alterações dele é mantido).

## Excluir parcelas lançadas por engano

Admin → **Parcelas** → marque as parcelas → ação **Remover parcelas selecionadas** → confirme.
Só o superusuário (admin) pode excluir. O sistema guarda o nosso número em **Títulos excluídos** e a importação
de extratos passa a ignorá-los; se excluiu por engano, apague o registro em **Títulos excluídos** e importe o extrato de novo.

## Backup (importante — são dados financeiros)

- O Neon grátis guarda só as últimas 6 horas para restaurar. Faça uma cópia local toda semana (e antes de
  mudanças grandes), com a `DATABASE_URL_UNPOOLED`:
  `docker run --rm -v "$PWD:/backup" postgres:17 pg_dump --format=custom --no-owner --no-acl --file=/backup/backup_AAAA-MM-DD.dump "URL_DIRETA_DO_NEON"`
- Guarde as cópias fora do computador também (ex.: um drive da empresa), nunca no GitHub.

## Alternativa: Docker (servidor próprio ou Railway)

O `Dockerfile` (com `entrypoint.sh` e `railway.json`) continua no projeto. Em uma VPS (Hetzner, DigitalOcean, Contabo, Locaweb etc.)
com Docker instalado:

```bash
docker network create carteira
docker run -d --name db --network carteira --restart unless-stopped \
  -e POSTGRES_DB=carteira -e POSTGRES_USER=carteira -e POSTGRES_PASSWORD=SENHA_DO_BANCO \
  -v carteira_pg:/var/lib/postgresql/data postgres:16
docker build -t carteira .
docker run -d --name web --network carteira --restart unless-stopped -p 8000:8000 --env-file .env carteira
```

com `DATABASE_URL=postgres://carteira:SENHA_DO_BANCO@db:5432/carteira` no `.env` (modelo em `.env.exemplo`).
Na VPS você também precisa de um proxy com HTTPS na frente (ex. Caddy ou Nginx + Let's Encrypt)
e de configurar os backups por conta própria.

## Variáveis de ambiente (referência)

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DJANGO_SECRET_KEY` | sim | chave secreta do Django |
| `DATABASE_URL` | sim | conexão com o PostgreSQL (na Vercel, criada pela integração do Neon, pelo pooler) |
| `DATABASE_URL_UNPOOLED` | na Vercel | conexão direta do Neon, usada nas migrações (criada pela integração) |
| `DJANGO_ALLOWED_HOSTS` | domínio próprio ou VPS | domínios permitidos, separados por vírgula (os domínios da Vercel e do Railway são lidos sozinhos) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | não | só se o site for acessado por um endereço diferente do que o navegador mostra (ex.: proxy extra) |
| `DJANGO_SUPERUSER_USERNAME` / `_PASSWORD` / `_EMAIL` | na 1ª instalação | cria o admin se ainda não existir |
| `DJANGO_SSL_REDIRECT` | não | `0` desliga o redirecionamento para HTTPS (padrão `1`) |
| `DJANGO_HSTS_SECONDS` | não | tempo do HSTS (padrão 3600) |
| `WEB_CONCURRENCY` | só Docker | nº de processos do gunicorn (padrão 2) |
| `DJANGO_DEBUG` | não | `1` só em desenvolvimento, **nunca** em produção |
