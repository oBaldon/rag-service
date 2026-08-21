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
5. Carrega URLs do `urls.env` e **ingere todas as `URL_*`**, sem interromper o lote quando uma delas falha
6. Executa o worker até acabar a fila (`jobs.status='queued'`)
7. Executa checagens rápidas no banco
8. (Opcional) exporta `nodes/chunks` em `.jsonl.gz`
9. Se alguma URL falhar, mantém um relatório JSONL em `storage/logs/`

O tipo documental é inferido automaticamente do parâmetro `tipo=` da URL.
Por exemplo, `RDC`, `INM`, `POR` e `RES` são persistidos como `rdc`, `inm`,
`por` e `res`. Se a URL não trouxer esse parâmetro, o ingest tenta reconhecer o
tipo pelo título; sem evidência suficiente, usa `norma`.

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
- `INGEST_REQUEST_DELAY_SECONDS` (default `1`) — intervalo entre URLs para evitar rajadas contra o site de origem
- `INGEST_FAILURE_LOG` — caminho opcional do relatório JSONL; por padrão usa um nome único em `storage/logs/`
- `FAIL_ON_INGEST_ERRORS` (default `1`) — após concluir ingest/index/export, retorna status `2` se houve falhas; use `0` para aceitar KB parcial com status de sucesso

As opções HTTP também podem ser configuradas no `.env`:

- `INGEST_HTTP_TIMEOUT_SECONDS` (default `30`)
- `INGEST_HTTP_MAX_ATTEMPTS` (default `3`)
- `INGEST_HTTP_BACKOFF_SECONDS` (default `2`)
- `INGEST_HTTP_MAX_BACKOFF_SECONDS` (default `60`)

Timeouts, erros de rede, HTTP 429 e HTTP 5xx usam espera exponencial e respeitam
`Retry-After` até o limite configurado. Erros permanentes, como HTTP 404, não são
repetidos. Cada linha do relatório é um JSON independente, por exemplo:

```json
{"timestamp":"2026-08-18T15:00:00+00:00","event":"ingest_failure","url":"https://exemplo.invalid/norma","stage":"fetch","error_type":"FetchFailure","message":"HTTP 404 ao buscar a URL (erro permanente)","http_status":404,"final_url":"https://exemplo.invalid/norma","attempts":1,"max_attempts":3,"retryable":false}
```

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

## Regressão de qualidade do retrieval

Após subir a API com o corpus indexado:

```bash
python golden/check_retrieval_quality.py --base-url http://127.0.0.1:8088
```

Os casos T01–T09 ficam em `golden/retrieval_quality_cases.json`. Eles verificam
identificadores exatos, conteúdo lexical, consulta conceitual e paráfrase.



## Pipeline semântico / vocabulário controlado

A evolução `mvp-v2-semantic-v1` adiciona vocabulário semântico regulatório,
expansão controlada da query e enriquecimento do input vetorial.

Validar o vocabulário:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary
```

Preparar reindexação sem recrawl:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v1
```

Enfileirar de fato:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v1   --execute
```

Depois que o worker concluir todos os jobs, configure a API com
`PIPELINE_VERSION=mvp-v2-semantic-v1` e rode novamente T01–T09.

Consulte `docs/semantic_vocabulary.md`.
