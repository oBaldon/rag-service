# Guia – Como usar a API do serviço RAG (InteliReg)

Este documento descreve, de forma prática, como consumir a API do serviço RAG (retrieval híbrido + resposta extrativa simples).

> **Base URL (produção local no mesmo host):** `http://127.0.0.1:8088`
> (equivalente a `http://localhost:8088` no próprio servidor)

---

## 1) Pré-requisitos

* Serviço rodando (FastAPI/Uvicorn).
* Ferramenta de chamada HTTP (ex.: `curl`, Postman/Insomnia, ou cliente HTTP na sua aplicação).
* (Recomendado) Chave de API configurada no servidor via variável de ambiente `RAG_API_KEY`.

---

## 2) Autenticação

A API pode exigir o header `x-api-key`.

* Se o servidor **não** tiver `RAG_API_KEY` configurada, a API pode aceitar chamadas sem chave (modo dev).
* Se o servidor **tiver** `RAG_API_KEY` configurada, você **deve** enviar:

```http
x-api-key: SEU_TOKEN_AQUI
```

> Em produção interna, recomenda-se **sempre** habilitar `RAG_API_KEY` e manter a API bindada apenas em `127.0.0.1`.

---

## 3) Headers úteis

### 3.1 Content-Type (obrigatório em POST)

```http
Content-Type: application/json
```

### 3.2 X-Request-Id (opcional)

Permite rastreabilidade ponta-a-ponta (logs/auditoria).

```http
X-Request-Id: req-20260302-0001
```

Se você não enviar, o serviço gera um UUID e devolve em `X-Request-Id` na resposta.

---

## 4) Endpoints disponíveis

### 4.1 Healthcheck

* **GET** `/health`
* Uso: validar se o serviço está no ar e verificar `pipeline_version`.

### 4.2 Retrieval (somente evidências)

* **POST** `/v1/rag/query`
* Uso: retorna **evidências** (chunks) relevantes, com metadados e citações.
* Não retorna resposta gerada por LLM; é um endpoint de **busca/recuperação**.

### 4.3 Ask (retrieval + resposta extrativa simples)

* **POST** `/v1/rag/ask`
* Uso: retorna `answer` (baseline extrativa, **não-LLM**) + `sources`.

### 4.4 Documentação automática (FastAPI)

* **GET** `/docs` (Swagger UI)
* **GET** `/redoc`
* **GET** `/openapi.json`

---

## 5) Parâmetros de retrieval (principais)

Os endpoints `/v1/rag/query` e `/v1/rag/ask` aceitam parâmetros para controlar o retrieval híbrido:

* `question` (string): pergunta do usuário (**obrigatório**).
* `version_id` (string/UUID, opcional): restringe a busca a uma versão específica.
* `n1_fts` (int, opcional): quantidade de candidatos via FTS (Full-Text Search).
* `n2_vec` (int, opcional): quantidade de candidatos via busca vetorial (pgvector).
* `rrf_k` (int, opcional): constante do RRF (fusão de rankings).
* `top_k` (int, opcional): quantos resultados finais retornar.

### Regras importantes (validação)

* Se `n1_fts <= 0` **e** `n2_vec <= 0`, a API retorna **HTTP 400**.
* Payload inválido (ex.: `question` vazia) retorna **HTTP 422**.

> Em geral: aumentar `n1_fts` e `n2_vec` melhora **recall**, mas pode trazer mais ruído e custo.

---

## 6) Exemplos práticos com `curl`

### 6.1 Healthcheck

```bash
curl -s http://127.0.0.1:8088/health
```

Resposta típica:

```json
{
  "ok": true,
  "service": "intelireg-rag",
  "pipeline_version": "mvp-v1"
}
```

---

## 7) Exemplos – `/v1/rag/query` (somente evidências)

> Nos exemplos abaixo, inclua `x-api-key` se o servidor estiver com `RAG_API_KEY` habilitada.

### 7.1 Query mínimo (só pergunta)

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "Quais são os requisitos para alteração pós-registro?"
  }'
```

Quando usar:

* Você quer **evidências** (trechos e metadados) para exibir no portal, gerar relatórios ou alimentar uma etapa LLM.

---

### 7.2 Query com parâmetros (FTS + vetorial + RRF)

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "Quais documentos costumam ser exigidos para AFE?",
    "n1_fts": 40,
    "n2_vec": 40,
    "rrf_k": 60,
    "top_k": 12
  }'
```

---

### 7.3 Query filtrando por `version_id`

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "O que diz o Art. 5º sobre prazos?",
    "version_id": "b7c4c8f2-1c2a-4c67-9db8-6fbcaa0d3a2a",
    "top_k": 8
  }'
```

---

### 7.4 Query com `X-Request-Id` (rastreabilidade)

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -H "X-Request-Id: req-20260302-0001" \
  -d '{
    "question": "Qual a definição de insumo farmacêutico ativo?",
    "top_k": 10
  }'
```

---

### 7.5 Desligar FTS ou vetorial (equivalente ao “golden/CLI”)

**Somente FTS (desliga vetorial):**

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "Quais documentos costumam ser exigidos para AFE?",
    "n1_fts": 80,
    "n2_vec": 0,
    "top_k": 10
  }'
```

**Somente vetorial (desliga FTS):**

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "Quais documentos costumam ser exigidos para AFE?",
    "n1_fts": 0,
    "n2_vec": 80,
    "top_k": 10
  }'
```

**Caso inválido (ambos desligados) → 400:**

```bash
curl -i -X POST http://127.0.0.1:8088/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "teste",
    "n1_fts": 0,
    "n2_vec": 0,
    "top_k": 5
  }'
```

---

## 8) Exemplos – `/v1/rag/ask` (retrieval + resposta extrativa)

### 8.1 Ask mínimo

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/ask \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "Resuma os principais pontos sobre CBPF."
  }'
```

### Estrutura típica de resposta (compatível com o código atual)

O endpoint retorna um JSON que inclui:

* `answer` como **objeto**:

  * `answer.text` (string)
  * `answer.cited_sources` (lista; quando aplicável)
* `sources` (lista de evidências com texto e metadados)

Exemplo de forma (simplificado):

```json
{
  "schema_version": 1,
  "run_type": "ask_rag",
  "run_id": "uuid",
  "query": "…",
  "filters": { "...": "..." },
  "params": { "...": "..." },
  "answer": {
    "text": "…texto extrativo…",
    "cited_sources": ["S1", "S2"]
  },
  "sources": [
    {
      "sid": "S1",
      "chunk_id": "…",
      "version_id": "…",
      "chunk_index": 1,
      "text": "…",
      "document": { "title": "…", "source_url": "…" },
      "citations": [ { "path": "...", "heading": "...", "node_id": "..." } ],
      "scores": { "rrf_score": 0.03, "fts_rank": 1, "vec_rank": 2 }
    }
  ]
}
```

> Observação: o `/ask` é útil como **debug/fallback**, mas no fluxo “RAG clássico com LLM” o recomendado é usar `/query` e deixar a geração final para o LLM.

---

### 8.2 Ask com “mais evidências” (maior recall)

```bash
curl -s -X POST http://127.0.0.1:8088/v1/rag/ask \
  -H "Content-Type: application/json" \
  -H "x-api-key: SEU_TOKEN_AQUI" \
  -d '{
    "question": "Liste exigências comuns em alteração pós-registro e aponte os trechos fonte.",
    "n1_fts": 60,
    "n2_vec": 60,
    "rrf_k": 80,
    "top_k": 15
  }'
```

---

## 9) Qual endpoint usar no fluxo com LLM (RAG clássico)

No InteliReg, o padrão esperado é o **fluxo RAG clássico**:

1. Usuário faz uma pergunta (`question`)
2. Serviço RAG executa o **retrieval** e retorna **evidências**
3. A aplicação (Portal/BFF) envia **pergunta + evidências** como contexto para o **LLM**
4. O LLM gera a resposta final, preservando **citações** e permitindo revisão humana

### Recomendação prática

* Use **`/v1/rag/query`** como endpoint padrão para alimentar o LLM (contexto + citações).
* Mantenha **`/v1/rag/ask`** como:

  * fallback/preview rápido sem LLM,
  * validação interna do retrieval (debug),
  * ou modo de operação quando o LLM estiver indisponível.

---

## 10) Dicas de uso (padrões recomendados)

1. **Portal/Frontend**

   * Use `/v1/rag/query` para exibir evidências com ranking e navegação.
   * Use `/v1/rag/ask` quando precisar de um “texto base” (extrativo) + evidências auditáveis.

2. **Rastreabilidade**

   * Sempre envie `X-Request-Id` gerado pelo BFF/Portal.

3. **Perfis de retrieval**

   * *Mais precisão*: reduzir `n1_fts`/`n2_vec` e `top_k`.
   * *Mais recall*: aumentar `n1_fts`/`n2_vec` e manter `top_k` moderado (ex.: 10–20).

---

## 11) Ver a especificação no Swagger (recomendado)

Abra no navegador:

* `http://127.0.0.1:8088/docs`

E, se precisar integrar via client generator:

* `http://127.0.0.1:8088/openapi.json`
* `http://127.0.0.1:8088/redoc`

