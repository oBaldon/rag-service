# Contrato HTTP v1 — Serviço RAG InteliReg

## 1. Objetivo

Este documento define o contrato estável usado pelo Portal InteliReg para
recuperação de evidências regulatórias.

O endpoint canônico para integração com o Laravel é:

```text
POST /v1/rag/query
```

O endpoint `/v1/rag/ask` permanece disponível como baseline extrativa e apoio
operacional, mas não substitui o fluxo RAG + LLM do Portal.

## 2. Identificadores

Cada requisição possui dois identificadores:

- `request_id`: correlação ponta a ponta, preferencialmente criada pelo Portal;
- `run_id`: identificador UUID da execução de retrieval.

Regras:

1. o serviço aceita `X-Request-Id`;
2. um valor ausente ou inválido é substituído por UUID;
3. `request_id` é devolvido no header e no JSON;
4. `run_id` é gerado uma única vez;
5. o `run_id` devolvido é o mesmo persistido em `rag_runs`;
6. `request_id` também é persistido em `rag_runs`.

## 3. Autenticação

Ambientes `local` e `test` podem usar:

```dotenv
RAG_AUTH_REQUIRED=false
```

Homologação e produção devem usar:

```dotenv
APP_ENV=production
RAG_AUTH_REQUIRED=true
RAG_API_KEY=<segredo-interno>
```

Quando a autenticação é obrigatória e a chave não está configurada, o serviço
fica `not_ready` e os endpoints protegidos retornam HTTP 503.

## 4. Query

### Requisição

```json
{
  "question": "Quais são os requisitos para alteração pós-registro?",
  "version_id": null,
  "n1_fts": 30,
  "n2_vec": 30,
  "rrf_k": 60,
  "top_k": 5
}
```

Campos `pipeline_version` e `embedding_model_id` são aceitos apenas para
compatibilidade. Quando enviados, precisam coincidir com a configuração do
servidor. O cliente não pode selecionar arbitrariamente outro pipeline ou
modelo.

### Limites padrão

| Campo | Limite |
|---|---:|
| `question` | 1–10.000 caracteres |
| `n1_fts` | 0–200 |
| `n2_vec` | 0–200 |
| `rrf_k` | 1–500 |
| `top_k` | 1–50 |

Ao menos um entre `n1_fts` e `n2_vec` deve ser maior que zero.

### Resposta

```json
{
  "schema_version": 1,
  "run_type": "query_rag",
  "request_id": "portal-analysis-123",
  "run_id": "69062489-490e-47bf-abd1-d316e661342b",
  "query": "Quais são os requisitos para alteração pós-registro?",
  "filters": {
    "version_id": null,
    "pipeline_version": "mvp-v1",
    "embedding_model_id": "intfloat/multilingual-e5-small@384"
  },
  "params": {
    "n1_fts": 30,
    "n2_vec": 30,
    "rrf_k": 60,
    "top_k": 5
  },
  "retrieval": {
    "version_id": null,
    "pipeline_version": "mvp-v1",
    "embedding_model_id": "intfloat/multilingual-e5-small@384",
    "n1_fts": 30,
    "n2_vec": 30,
    "rrf_k": 60,
    "top_k": 5
  },
  "generated_at": "2026-07-31T20:00:00Z",
  "results": []
}
```

## 5. Evidências

Cada item de `results` contém:

```text
rank
scores
chunk
document
citations
```

Campos canônicos para o adapter Laravel:

| RAG | EvidenceDTO |
|---|---|
| `chunk.chunk_id` | `chunk_id` |
| `chunk.version_id` | `version_id` |
| `chunk.chunk_index` | `chunk_index` |
| `chunk.text` | `content` |
| `document.document_id` | `document_id` |
| `document.title` | `title` |
| `document.source_org` | `source_org` |
| `document.doc_type` | `document_type` |
| `document.final_url` ou `source_url` | `url` |
| `scores.rrf_score` | `score` |
| `citations` | `citations` |

O Portal deve persistir também um snapshot do texto efetivamente usado.

## 6. Ask

O `/v1/rag/ask` utiliza `source_id` como identificador canônico de fonte.

O campo `sid` é mantido como alias legado durante o contrato v1.

```json
{
  "source_id": "S1",
  "sid": "S1"
}
```

## 7. Health checks

```text
GET /health
GET /health/live
GET /health/ready
```

`/health` é um alias compatível de liveness.

O readiness verifica:

- configuração;
- PostgreSQL;
- extensão pgvector;
- tabelas obrigatórias;
- modelo de embedding configurado.

## 8. Erros

Formato padronizado:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "A requisição contém parâmetros inválidos.",
    "request_id": "uuid",
    "details": []
  }
}
```

Detalhes internos, credenciais e stack traces não são devolvidos ao consumidor.

## Retrieval Quality v2 (campos aditivos)

`/v1/rag/query` mantém o contrato v1 e acrescenta diagnóstico opcional:

- `retrieval.strategy_version`
- `retrieval.candidate_limit`
- `retrieval.effective_n1_fts`
- `retrieval.effective_n2_vec`
- `retrieval.identifier`
- `scores.final_score`
- `scores.lexical_coverage`
- `scores.exact_identifier_match`
- `scores.exact_identifier_rank`

`rrf_score` não foi redefinido: continua representando somente Reciprocal Rank
Fusion entre FTS e vetor. `final_score` é o sinal usado no reranking final.



## Retrieval Quality v3 — vocabulário semântico (campos aditivos)

O contrato HTTP continua em `schema_version=1`. A estratégia de retrieval pode
expor adicionalmente:

- `retrieval.semantic_expansion`
- `retrieval.semantic_passage_enrichment_enabled`
- `retrieval.semantic_concept_lookup_enabled`
- `scores.semantic_concept_coverage`
- `scores.semantic_vocabulary_score`
- `scores.semantic_concepts_matched`
- `scores.semantic_lookup_match`
- `scores.semantic_lookup_rank`

`retrieval.semantic_expansion` registra versão/hash do vocabulário e os termos
controlados adicionados ao retrieval. A pergunta canônica permanece no campo
top-level `query`.

Os sinais semânticos são auxiliares de ranking e **não representam
probabilidade, vigência ou confiança regulatória**.


## Contexto regulatório curado por documento

A partir do RAG-QUALITY-03, `document` pode incluir
`regulatory_context`. Esse campo é informativo e **não altera o ranking**.

Quando não há assertion aprovada:

```json
{
  "regulatory_context": {
    "document_id": "uuid",
    "status": {
      "status": "unknown",
      "basis": "no_approved_curated_assertion",
      "assertion_id": null,
      "assertion_key": null,
      "effective_from": null,
      "valid_to": null,
      "source_url": null,
      "evidence_version_id": null,
      "evidence_note": null,
      "reviewed_by": null,
      "reviewed_at": null
    },
    "relations": []
  }
}
```

`unknown` significa ausência de curadoria aprovada no InteliReg, não conclusão
sobre vigência perante a autoridade regulatória.

Somente `regulatory_status_assertions` e `regulatory_relations` com
`review_status=approved` são expostos. Relações carregam `direction=outbound`
quando o documento retornado é a origem canônica e `direction=inbound` quando é
o alvo.
