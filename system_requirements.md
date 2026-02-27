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
- `vector` (pgvector) para embeddings (`VECTOR(384)` no MVP atual)

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
- `sentence-transformers`
- `fastapi`
- `uvicorn[standard]`
- **torch (PyTorch)**

### PyTorch (torch) – observação importante
O módulo de embeddings usa `sentence-transformers`, que depende de **PyTorch** para inferência.

- Em servidores **sem GPU** ou com driver CUDA incompatível, recomenda-se instalar a variante **CPU-only** do PyTorch.
- Caso contrário, é comum aparecer warning de inicialização CUDA (não impede funcionamento em CPU, mas polui logs).

> Em geral, manter o `torch` dentro do **venv** é o recomendado para reprodutibilidade.

## 6) Variáveis de ambiente

Obrigatória:
- `DATABASE_URL`
  - Exemplo:
    - `postgresql://intelireg:intelireg@localhost:5555/intelireg`

Para a API interna:
- `RAG_API_KEY` (chave de serviço-a-serviço para chamadas internas)

Recomendadas (para reduzir warnings e estabilizar downloads):
- `HF_TOKEN` (Hugging Face token de leitura) para evitar rate limit e warning de acesso não autenticado
- `CUDA_VISIBLE_DEVICES=` (vazio) para forçar CPU
- `TOKENIZERS_PARALLELISM=false` para reduzir ruído em logs

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

### Warning do CUDA driver (servidor sem driver compatível)
Se aparecer algo como “CUDA initialization: The NVIDIA driver on your system is too old”:
- Solução recomendada: instalar **PyTorch CPU-only** no venv.
- Alternativamente: manter `CUDA_VISIBLE_DEVICES=` e seguir (o warning não impede execução em CPU).

### Índice HNSW não disponível
Se seu pgvector for antigo, `USING hnsw` pode não existir.
- O schema/boot ideal deve tratar isso (ou você remove o HNSW e usa um índice alternativo / nenhum índice no MVP).