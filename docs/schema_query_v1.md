# InteliReg — Schema de Saída do query_rag (v1)

## Objetivo
Definir o **contrato estável** do JSON produzido por `intelireg.cli.query_rag`, que serve como **input para o LLM** (fora do escopo do MVP).
O MVP termina em: **retrieval híbrido (FTS + vetorial) + RRF + evidências (citations/node_refs)**.

Este schema é **versionado** por `schema_version`. Alterações incompatíveis exigem nova versão.

---

## Metadados (top-level)

### Campos obrigatórios
- `schema_version` (int)  
  - Valor fixo nesta versão: `1`.
- `run_type` (string)  
  - Valor fixo nesta versão: `"query_rag"`.
- `query` (string)  
  - Pergunta do usuário.
- `retrieval` (object)  
  - Parâmetros canônicos do retrieval (ver seção abaixo).
- `generated_at` (string, ISO-8601 UTC)  
  - Timestamp de geração.
- `results` (array)  
  - Lista ordenada por rank (ver seção “Results”).

### Campos auditáveis recomendados (presentes no MVP)
- `run_id` (string, UUID)  
- `output_path` (string)

### Campos legados (mantidos por compatibilidade)
> Estes campos podem existir, mas **não são o contrato canônico**.  
- `filters` (object)
- `params` (object)

---

## Retrieval (contrato canônico)
`retrieval` é o bloco canônico de configuração que o consumidor (LLM/serviço) deve usar.

### Campos obrigatórios
- `pipeline_version` (string)
- `embedding_model_id` (string)
- `n1_fts` (int) — candidatos FTS
- `n2_vec` (int) — candidatos vetoriais (0 desativa)
- `rrf_k` (int) — parâmetro do RRF
- `top_k` (int) — tamanho final retornado
- `version_id` (string|null) — filtro opcional de versão

---

## Results
`results` é uma lista de objetos, ordenados por relevância (rank ascendente).

### Campos obrigatórios por item
- `rank` (int) — começa em 1
- `scores` (object) — breakdown canônico dos sinais de ranking
- `chunk` (object) — evidência textual retornada
- `document` (object) — metadados do documento
- `citations` (array) — evidências estruturais (node_refs)

### Scores (object)
Campos obrigatórios:
- `rrf_score` (number)
- `fts_rank` (int|null)
- `fts_score` (number|null)
- `vec_rank` (int|null)
- `vec_distance` (number|null)

> Observação: em cenários onde um sinal não contribui (ex.: `n2_vec=0`), os campos podem vir `null`.

### Chunk (object)
Campos obrigatórios:
- `chunk_id` (string)
- `version_id` (string)
- `chunk_index` (int)
- `tokens_count` (int)
- `text` (string) — conteúdo do chunk (trecho fonte)

### Document (object)
Objeto com metadados do documento. Campos podem variar conforme fonte.
Recomendado manter pelo menos:
- `document_id` (string|int, se existir)
- `title` (string, se existir)
- `source_org` (string, se existir)
- `doc_type` (string, se existir)
- `url` (string, se existir)

### Citations (array)
Lista de referências estruturais do chunk para nodes originais.
Formato mínimo esperado: array de objetos (ou estruturas) que permitam localizar evidências na fonte.
Ex.: `node_id`, `path`, `heading_text`, offsets/trechos quando disponível.

---

## Regras de estabilidade
- O consumidor deve tratar `schema_version=1` como contrato fixo.
- Campos `run_id`, `output_path` e `generated_at` variam por execução.
- Campos fora do contrato podem ser adicionados, desde que não removam/alterem os obrigatórios.
- Mudanças incompatíveis exigem `schema_version` novo.

---

## Exemplo mínimo (shape)
```json
{
  "schema_version": 1,
  "run_type": "query_rag",
  "query": "string",
  "retrieval": {
    "version_id": null,
    "pipeline_version": "mvp-v1",
    "embedding_model_id": "intfloat/multilingual-e5-small@384",
    "n1_fts": 50,
    "n2_vec": 10,
    "rrf_k": 60,
    "top_k": 5
  },
  "generated_at": "2026-02-19T00:00:00Z",
  "run_id": "uuid",
  "output_path": "storage/runs/....json",
  "results": [
    {
      "rank": 1,
      "scores": {
        "rrf_score": 0.123,
        "fts_rank": 1,
        "fts_score": 0.9,
        "vec_rank": 4,
        "vec_distance": 0.23
      },
      "chunk": {
        "chunk_id": "string",
        "version_id": "string",
        "chunk_index": 12,
        "tokens_count": 180,
        "text": "..."
      },
      "document": {},
      "citations": []
    }
  ]
}
