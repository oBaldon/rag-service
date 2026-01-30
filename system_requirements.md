<!-- system_requirements.md -->

# System requirements (InteliReg)

Este projeto depende de alguns componentes do sistema além do `pip install -r requirements.txt`.

## 1) Requisitos mínimos

### Sistema operacional
- Linux (recomendado) ou macOS.
- Windows funciona via WSL2 (recomendado) se você precisar.

### Python
- Python **3.11+**
- `venv`/virtualenv habilitado

### PostgreSQL
- PostgreSQL **14+** (recomendado **15+**)
- `psql` (client) disponível no PATH

### Extensões do Postgres
O schema usa:
- `pgcrypto` (para `gen_random_uuid()`)
- `unaccent` (para busca textual / normalização)
- `vector` (pgvector) para embeddings (`VECTOR(1536)`)

> Observação importante: `vector` (pgvector) frequentemente exige **superuser** para `CREATE EXTENSION`.
> O bootstrap já tenta e, se não conseguir, imprime o comando para rodar uma vez como superuser.

## 2) Pacotes do sistema (Linux/Ubuntu – referência)

Você pode precisar de:
- `postgresql` (server)
- `postgresql-client` (psql)
- `postgresql-contrib` (inclui `unaccent` em várias distros)
- **pgvector** para a mesma versão do seu Postgres (ex.: pacote `postgresql-15-pgvector`)
- `git`, `curl` (opcional)
- (opcional) `build-essential` caso você troque `psycopg[binary]` por build-from-source

> Os nomes exatos dos pacotes variam por distro. O ponto principal é: Postgres + contrib + pgvector.

## 3) Setup do Postgres (roles/db)

Você precisa de:
- Um banco (ex.: `intelireg`)
- Um usuário/role (ex.: `intelireg`) com permissão para criar tabelas no banco

Exemplo (executar como superuser `postgres`):
- criar role com senha
- criar database com owner `intelireg`
- (opcional) ajustar porta (ex.: 5555) no `postgresql.conf`

**Porta 5555**: se você está usando `localhost:5555`, garanta que seu Postgres está escutando nessa porta.

## 4) Extensão pgvector (primeira vez)

Se o `bootstrap_db.sh` reclamar de permissão, rode **uma vez** como superuser:

- `psql -U postgres -p 5555 -d intelireg -c "CREATE EXTENSION IF NOT EXISTS vector;"`

Depois disso, o usuário normal (`intelireg`) costuma conseguir rodar as migrations sem problemas.

## 5) Python deps

Instalação padrão:

- `python -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`

O `requirements.txt` atual inclui:
- `httpx`
- `beautifulsoup4`
- `psycopg[binary]`
- `python-dotenv`

## 6) Variáveis de ambiente

Obrigatória:
- `DATABASE_URL`
  - Exemplo:
    - `postgresql://intelireg:intelireg@localhost:5555/intelireg`

Opcionais (dependendo do que você habilitar no futuro):
- credenciais/chaves de LLM/embeddings, caso você substitua o embedding fake por provedor real.

## 7) Bootstrap do banco

Com o `DATABASE_URL` configurado:

- `./scripts/bootstrap_db.sh --db "$DATABASE_URL"`

Se você quiser permitir que o script crie extensões como superuser automaticamente, forneça também:

- `./scripts/bootstrap_db.sh --db "$DATABASE_URL" --superuser-url "postgresql://postgres:...@localhost:5555/intelireg"`

Ou exporte:
- `export PG_SUPERUSER_URL="postgresql://postgres:...@localhost:5555/intelireg"`

## 8) Problemas comuns

### “role <usuario> does not exist”
Você rodou `psql` sem `DATABASE_URL` (o `psql` tenta usar o usuário do sistema).
- Solução: sempre use `psql "$DATABASE_URL"` ou `psql "$DB"`.

### “permission denied to create extension vector”
Normal: pgvector costuma exigir superuser.
- Solução: criar `vector` uma vez como superuser e seguir.

### Índice HNSW não disponível
Se seu pgvector for antigo, `USING hnsw` pode não existir.
- O schema/boot ideal deve tratar isso (ou você remove o HNSW e usa um índice alternativo / nenhum índice no MVP).
