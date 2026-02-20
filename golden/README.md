# Golden Set — InteliReg (MVP)

Esta pasta contém os **scripts do golden set** do InteliReg para o MVP, divididos em:

- **01_update_kb.sh (assíncrono / batch)**: atualiza a “KB” (ingest + index) no banco.
- **02_query_rag.sh (síncrono / online)**: executa uma pergunta e gera o JSON de retrieval (contrato v1).

> **Premissa do MVP:** a saída final é o **JSON de retrieval híbrido (FTS + vetorial + RRF)** que servirá como *input* para LLM (LLM fora do escopo).

---

## Pré-requisitos

- `.env` na raiz do projeto (com `DATABASE_URL`, `PG_SUPERUSER_URL`, `PYTHONPATH=src`, etc.)
- Banco Postgres rodando (porta/config conforme `.env`)
- `psql` disponível
- Ambiente Python (venv) com dependências instaladas
- `jq` é recomendado (para visualização), mas não é obrigatório

---

## Arquivos

- `golden/urls.env`  
  Arquivo com **N URLs** no formato `export URL_*="https://..."`  
  Exemplo:
  ```bash
  export URL_327="https://..."
  export URL_875="https://..."
  export URL_938="https://..."
  ```

- `golden/01_update_kb.sh`  
  Atualiza KB (reset opcional → ingest → index → checagens).

- `golden/02_query_rag.sh`  
  Consulta síncrona (recebe pergunta via argumento) e gera JSON em `storage/runs/`.

---

## Permissões de execução (uma vez)

```bash
chmod +x golden/01_update_kb.sh golden/02_query_rag.sh
```

---

## 01_update_kb.sh — Atualização da KB (batch)

### O que faz
1. Carrega `.env`
2. (Opcional) reseta banco e reaplica bootstrap/migrations
3. Garante extensão `vector` (pgvector) quando necessário
4. Detecta os módulos executáveis de ingest e worker
5. Carrega URLs do `urls.env` e **ingere todas as `URL_*`**
6. Executa o worker até acabar a fila (`jobs.status='queued'`)
7. Executa checagens rápidas no banco
8. (Opcional) exporta `nodes/chunks` em `.jsonl.gz`

### Comando padrão (reset + ingest + index, sem export)
```bash
DO_RESET=1 DO_INGEST=1 DO_INDEX=1 EXPORT_ALL=0 \
URLS_FILE=golden/urls.env \
./golden/01_update_kb.sh
```

### Rodar sem reset (modo “rotina diária incremental”)
```bash
DO_RESET=0 DO_INGEST=1 DO_INDEX=1 EXPORT_ALL=0 \
URLS_FILE=golden/urls.env \
./golden/01_update_kb.sh
```

### Rodar com export (gera arquivos em `storage/`)
```bash
DO_RESET=1 DO_INGEST=1 DO_INDEX=1 EXPORT_ALL=1 \
URLS_FILE=golden/urls.env \
./golden/01_update_kb.sh
```

### Variáveis suportadas
- `DO_RESET` (default `1`) — executa `./scripts/reset_db.sh --yes`
- `DO_INGEST` (default `1`) — executa ingest das URLs
- `DO_INDEX` (default `1`) — processa jobs de index até acabar fila
- `EXPORT_ALL` (default `1`) — exporta `nodes/chunks` em `.jsonl.gz` (quando `1`)
- `URLS_FILE` (default `golden/urls.env`) — arquivo com `export URL_*="..."`

---

## 02_query_rag.sh — Query síncrona (online)

### O que faz
1. Carrega `.env`
2. Verifica se a KB está indexada (existem `embedding_chunks` e `chunk_embeddings`)
3. Executa `intelireg.cli.query_rag` com a pergunta recebida por argumento
4. Imprime o caminho do JSON gerado e um resumo via `jq` (se disponível)

### Uso
```bash
./golden/02_query_rag.sh "quais requisitos para produto de cannabis ter até 0,2% de THC?"
```

### Alterar TOPK (opcional)
```bash
TOPK=10 ./golden/02_query_rag.sh "minha pergunta..."
```

### Saída
- JSON gravado em: `storage/runs/<YYYYMMDD>_<runid>_query.json`
- O script imprime também o caminho do arquivo (útil para pipe/integração).

---

## Fluxo recomendado

### Rotina diária (assíncrona)
1. Atualizar `golden/urls.env` (novas normas/URLs)
2. Rodar:
   ```bash
   DO_RESET=0 DO_INGEST=1 DO_INDEX=1 EXPORT_ALL=0 URLS_FILE=golden/urls.env ./golden/01_update_kb.sh
   ```

### Pergunta do usuário (síncrona)
Rodar:
```bash
./golden/02_query_rag.sh "sua pergunta aqui"
```

---

## Contrato do JSON (input do LLM)
O `query_rag` gera JSON com `schema_version=1` e bloco canônico `retrieval`.  
Ver: `docs/schema_query_v1.md`
