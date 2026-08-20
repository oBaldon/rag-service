# Retrieval Quality v2 — InteliReg RAG

## Objetivo

Esta evolução mantém o retrieval híbrido existente (FTS + vetor + RRF), mas
corrige lacunas observadas na bateria T01–T09 executada contra o corpus completo.

Os principais achados foram:

- consultas por identificador normativo completo (tipo + número + data) não
  priorizavam o ato exato;
- consultas com forte sobreposição lexical funcionavam bem;
- consultas conceituais também podiam funcionar bem;
- paráfrases semanticamente mais distantes ainda precisam de investigação no
  canal vetorial;
- em temas competitivos, o documento correto podia entrar no recall, porém
  ficar baixo no ranking final.

## Mudanças desta versão

### 1. Regulatory Identifier Resolver

Consultas como:

- `RDC nº 476, de 10/03/2021`
- `Portaria - PRT nº 1.520, de 17/09/2019`
- `IN nº 352, de 18/03/2025`

são interpretadas como estrutura, e não apenas como linguagem natural.

O parser extrai:

- família normativa;
- número;
- data, quando fornecida;
- ano.

A resolução é feita contra títulos de documentos já indexados. Não exige
reindexação do corpus atual.

A correspondência exata recebe um sinal separado de `rrf_score`; o RRF continua
representando apenas a fusão FTS/vetorial.

### 2. FTS preserva identificadores

O fallback antigo removia explicitamente pares como `RDC 476` e descartava
números longos. Para uma base regulatória isso eliminava informação crítica.

Quando um identificador é detectado, tipo, número e ano passam a ser preservados
na keywordização do FTS.

### 3. Pool de candidatos + reranking determinístico

O SQL deixa de cortar diretamente em `top_k`. Um pool maior é recuperado antes
da ordenação final. Com reranking ativo, os canais FTS e vetorial também podem
ser ampliados deterministicamente até o `candidate_limit`, respeitando os
limites de segurança da API. A resposta registra `effective_n1_fts` e
`effective_n2_vec` para auditoria.

O reranking usa somente sinais auditáveis:

- RRF;
- correspondência exata de identificador;
- cobertura lexical leve entre pergunta e título/chunk.

Não há LLM ou decisão normativa nesta etapa.

### 4. Diversificação

Existe um soft cap configurável por documento, mas ele permanece desligado por
padrão. Os testes T05/T09 mostraram que vários artigos do mesmo ato podem ser
legitimamente úteis; por isso diversidade não deve ser forçada antes de medir.

### 5. Diagnóstico

Cada resultado agora pode expor, em `scores`:

- `rrf_score`;
- `fts_rank` / `fts_score`;
- `vec_rank` / `vec_distance`;
- `final_score`;
- `lexical_coverage`;
- `exact_identifier_match`;
- `exact_identifier_rank`.

A resposta `/v1/rag/query` também registra a estratégia utilizada e o
identificador detectado.

Para diagnóstico local sem gravar `rag_run`:

```bash
PYTHONPATH=src python -m intelireg.cli.diagnose_retrieval \
  "RDC nº 476, de 10/03/2021" --top-k 12
```

Para separar o comportamento dos canais lexical e vetorial:

```bash
PYTHONPATH=src python -m intelireg.cli.diagnose_retrieval \
  "Quais atos regulatórios disponíveis tratam das atribuições de representantes brasileiros em fóruns internacionais de harmonização farmacêutica?" \
  --top-k 50 --compare-channels
```

Os blocos `fts_only` e `vector_only` são executados sem reranking para permitir
inspeção direta do canal que está perdendo o candidato esperado.

## Regressão T01–T09

Com API ativa:

```bash
python golden/check_retrieval_quality.py \
  --base-url http://127.0.0.1:8088
```

O script retorna código `0` somente se todos os casos passarem.

A suíte é deliberadamente um benchmark de retrieval, não validação regulatória.
A conferência de vigência, aplicabilidade e interpretação final continua fora
do escopo automático.

## Próximos passos

Se T08 continuar falhando após esta versão, o próximo diagnóstico deve comparar
`fts_rank` e `vec_rank` da Portaria 1.520/2019 e da Portaria 539/2024. Só então
deve ser tomada decisão sobre:

- ampliação do candidate pool vetorial;
- enriquecimento de passages com metadados/título;
- mudança de modelo de embedding;
- reranker neural.

Trocar o embedding sem essa medição exigiria reindexação completa e não é
recomendado como primeira resposta.
