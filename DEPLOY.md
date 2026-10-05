# Colocar o sistema no ar

O projeto roda em qualquer hospedagem que aceite **Docker** (há um `Dockerfile` na raiz).
Ao iniciar, o container aplica as migrações do banco, cria o usuário admin (se configurado) e sobe o servidor
(gunicorn). A hospedagem verifica se o sistema está no ar pela rota `/saude/`.

## Opção recomendada: Railway

Faz o deploy direto do GitHub, tem PostgreSQL gerenciado e HTTPS automático.

**Plano:** comece pelo **Grátis** (teste de 30 dias com US$ 5 de crédito, sem cartão) para montar e
testar tudo. Antes de acabar o teste, passe para o **Hobby (US$ 5/mês, já inclui US$ 5 de uso)**:
depois do teste o plano grátis dá só US$ 1/mês de crédito, que não sustenta o sistema + banco
ligados o mês inteiro, e os serviços param quando o crédito acaba.
Confira os valores atuais em railway.com/pricing.

1. Crie a conta em https://railway.com entrando com o GitHub.
2. **New Project → Deploy from GitHub repo →** escolha o seu fork deste repositório
   (autorize o Railway a acessar o repositório, se pedir). Ele usa o branch `main`.
   O primeiro deploy vai falhar por falta das variáveis — é normal, siga os próximos passos.
3. No mesmo projeto: **+ Create (ou botão direito no canvas) → Database → PostgreSQL**.
4. Clique no card do **sistema** (o que tem o nome do repositório, não o do Postgres) →
   aba **Variables** → **Raw Editor**, cole o bloco abaixo trocando os valores em MAIÚSCULAS e clique em **Update Variables**:

   ```
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   DJANGO_SECRET_KEY=COLE_AQUI_UMA_CHAVE_ALEATORIA_LONGA
   DJANGO_SUPERUSER_USERNAME=admin
   DJANGO_SUPERUSER_PASSWORD=ESCOLHA_UMA_SENHA_FORTE
   DJANGO_SUPERUSER_EMAIL=SEU_EMAIL
   WEB_CONCURRENCY=1
   ```

   - `DJANGO_SECRET_KEY`: 50+ caracteres aleatórios. Gere com
     `python -c "import secrets; print(secrets.token_urlsafe(50))"` ou um gerador de senhas. Nunca compartilhe.
   - `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`: o usuário e a senha que **você** escolhe
     para entrar no sistema. O usuário é criado no primeiro deploy.
   - `WEB_CONCURRENCY=1`: um processo só, gasta menos memória (suficiente para um escritório).
   - Se o banco tiver outro nome no canvas (não "Postgres"), ajuste `${{Postgres.DATABASE_URL}}`.
5. Aba **Settings → Networking → Generate Domain**. Em "porta", use a que aparece no log do deploy
   na linha `Listening at: http://0.0.0.0:XXXX` (no Railway normalmente **8080**). Se errar a porta, o site
   não abre ("Application failed to respond"): edite a porta do domínio no mesmo lugar.
   Não precisa configurar o domínio em variável: o sistema lê o domínio gerado pelo Railway sozinho.
6. Aguarde o deploy ficar verde (**Deployments**), abra o endereço gerado, entre com o usuário e a senha
   do passo 4 e vá em **Importar extrato** para enviar o `exemplos/extrato_exemplo.txt`
   (ou um extrato mais novo do banco).
7. Depois de entrar a primeira vez, apague a variável `DJANGO_SUPERUSER_PASSWORD`
   (o usuário já existe; troque a senha pelo próprio sistema em "Alterar senha").

Cada merge no `main` gera um novo deploy automaticamente.

### Domínio próprio (opcional)

Use um **subdomínio**, por exemplo `financeiro.suaconstrutora.com.br`. O domínio "raiz" (`suaconstrutora.com.br`)
normalmente já é usado pelo site e pelos e-mails da empresa e não aceita CNAME na maioria dos provedores.

1. **Railway:** card do sistema → **Settings → Networking → + Custom Domain**, digite
   `financeiro.suaconstrutora.com.br` e a porta **8080**. O Railway mostra os registros de DNS a criar:
   um **CNAME** (nome `financeiro`, valor `xxxx.up.railway.app`) e, às vezes, um **TXT** de verificação.
2. **Provedor do domínio** (Registro.br, GoDaddy, Hostinger, Cloudflare...): na zona de DNS do domínio,
   crie exatamente os registros mostrados pelo Railway. No Registro.br: domínio → **DNS → Editar zona**
   (se aparecer "modo avançado", ative) → **Nova entrada** → tipo CNAME. Na Cloudflare, deixe a nuvem
   **cinza** ("DNS only").
3. **Variável no sistema:** aba **Variables** → `DJANGO_ALLOWED_HOSTS=financeiro.suaconstrutora.com.br`
   → aplique as mudanças. O endereço `.up.railway.app` continua funcionando.
4. Aguarde a propagação do DNS (de minutos a algumas horas). Quando o Railway mostrar o domínio como
   verificado, o certificado HTTPS é emitido sozinho e o site abre em `https://financeiro.suaconstrutora.com.br`.

Se abrir com "Bad Request (400)", falta (ou está diferente) o `DJANGO_ALLOWED_HOSTS`.

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

- No Railway, ative os backups do PostgreSQL (aba **Backups** do serviço do banco).
- Além disso, faça periodicamente uma cópia local:
  `pg_dump "URL_PUBLICA_DO_BANCO" > backup_AAAA-MM-DD.sql`
  (a URL pública fica em Postgres → Variables → `DATABASE_PUBLIC_URL`).

## Alternativa: servidor próprio (VPS)

Em uma VPS (Hetzner, DigitalOcean, Contabo, Locaweb etc.) com Docker instalado:

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
e de configurar os backups por conta própria — por isso o Railway é a opção recomendada.

## Variáveis de ambiente (referência)

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DJANGO_SECRET_KEY` | sim | chave secreta do Django |
| `DATABASE_URL` | sim | conexão com o PostgreSQL |
| `DJANGO_ALLOWED_HOSTS` | fora do Railway ou domínio próprio | domínios permitidos, separados por vírgula (no Railway o domínio gerado é lido sozinho) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | não | só se o site for acessado por um endereço diferente do que o navegador mostra (ex.: proxy extra) |
| `DJANGO_SUPERUSER_USERNAME` / `_PASSWORD` / `_EMAIL` | no 1º deploy | cria o admin se ainda não existir |
| `DJANGO_SSL_REDIRECT` | não | `0` desliga o redirecionamento para HTTPS (padrão `1`) |
| `DJANGO_HSTS_SECONDS` | não | tempo do HSTS (padrão 3600) |
| `WEB_CONCURRENCY` | não | nº de processos do gunicorn (padrão 2) |
| `DJANGO_DEBUG` | não | `1` só em desenvolvimento, **nunca** em produção |
