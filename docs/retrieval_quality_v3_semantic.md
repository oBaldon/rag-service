# Retrieval Quality v3 — Semantic Recall

## Motivação

A bateria T01–T09 após `Retrieval Quality v2` passou em 8/9 casos.

O único caso residual, T08, demonstrou uma lacuna específica:

```text
"atribuições de representantes brasileiros em fóruns internacionais
 de harmonização farmacêutica"
```

não recuperava as Portarias relacionadas ao ICH, embora consultas contendo a
âncora literal `ICH` funcionassem muito bem.

Os diagnósticos mostraram que o candidato esperado não recebia sinal forte nem
do FTS nem do vetor. Ajustar apenas o peso do RRF não resolveria ausência de
recall.

## Estratégia v3

A versão v3 adiciona um vocabulário semântico controlado e três mecanismos:

1. expansão determinística da query usada pelo embedding;
2. canal de recall por conceitos/aliases controlados;
3. enriquecimento opcional dos inputs de embedding dos passages.

O FTS continua operando sobre a pergunta original.

## Score final

O RRF continua sendo calculado exclusivamente de FTS + vetor.

O `final_score` pode adicionar sinais separados:

```text
RRF
+ cobertura lexical
+ cobertura de conceito semântico
+ exact identifier boost
```

Nenhum desses campos é apresentado como probabilidade.

## Regressão

Após ativação do pipeline semântico:

```bash
python golden/check_retrieval_quality.py   --base-url http://127.0.0.1:8088
```

Critério:

- T01–T04 continuam rank 1;
- T05, T07 e T09 não podem regredir;
- T06 permanece top 3;
- T08 deve passar em top 3.

## Reindexação

O concept lookup e a expansão da query funcionam mesmo contra os vetores
anteriores, mas o enriquecimento de passage só passa a valer após reindexação.

Para preparar o pipeline novo sem recrawl:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v1   --execute
```

Consuma os jobs com o worker e, somente depois, altere a API para:

```dotenv
PIPELINE_VERSION=mvp-v2-semantic-v1
```

Mais detalhes em `docs/semantic_vocabulary.md`.


## Reprodutibilidade do índice

A tabela `index_profiles` impede que embeddings produzidos com aliases ou
`embedding_terms` diferentes sejam misturados sob a mesma `PIPELINE_VERSION`.

O worker falha de forma explícita se o perfil carregado divergir daquele
registrado para o pipeline/modelo. A correção é criar uma nova versão de
pipeline e reindexar; não sobrescrever silenciosamente o perfil existente.
