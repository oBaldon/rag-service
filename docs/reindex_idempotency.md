# Idempotência da fila de reindexação

## Objetivo

Evitar que duas execuções de `enqueue_reindex_all` criem jobs ativos duplicados
para a mesma combinação:

- `version_id`
- `pipeline_version`

A regra é aplicada no banco e no enfileirador.

## Garantia no PostgreSQL

A migration `0004_index_job_idempotency.sql` adiciona:

- `jobs.idempotency_key`;
- backfill para `IndexVersionJob` já existentes;
- índice único parcial `uq_jobs_active_idempotency`.

A unicidade vale somente para jobs em:

- `queued`
- `running`
- `failed`

Jobs `done` e `dead` liberam a chave. Isso permite uma reindexação futura
explícita sem apagar o histórico.

A chave segue o formato:

```text
IndexVersionJob:<pipeline_version>:<version_id>
```

## Comportamento do enfileirador

O dry-run agora separa:

- `missing_pipeline`: versões sem chunks no pipeline;
- `already_active`: versões sem chunks, mas com job ativo;
- `eligible_to_enqueue`: versões sem chunks e sem job ativo;
- `selected_to_enqueue`: quantidade após `--limit`;
- `active_duplicate_groups`: grupos duplicados ativos detectados.

Exemplo:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all \
  --pipeline-version mvp-v2-semantic-v1
```

A execução real usa seleção + `INSERT ... SELECT` dentro da mesma transação:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all \
  --pipeline-version mvp-v2-semantic-v1 \
  --execute
```

Mesmo que dois processos executem simultaneamente, o índice único parcial
impede a criação de dois jobs ativos com a mesma chave.

## Smoke test

Depois de aplicar a migration:

```bash
PYTHONPATH=src python golden/check_reindex_idempotency.py
```

O teste roda dentro de uma transação e faz rollback. Nenhum job de teste fica
persistido.

Resultado esperado:

```json
{
  "status": "PASS",
  "first_insert_created": true,
  "second_insert_created": false,
  "active_count_inside_transaction": 1,
  "rollback": true
}
```

## Readiness

`/health/ready` passa a expor `index_queue`.

Se houver mais de um `IndexVersionJob` ativo para a mesma versão/pipeline,
o componente aparece como `degraded`. O serviço de consulta continua
operacional, pois a duplicidade da fila não invalida o retrieval já indexado.

## Regra operacional

Não usar exclusão manual de chunks para “forçar” nova indexação. Para uma
reindexação intencional, use uma nova `PIPELINE_VERSION` ou deixe o job anterior
chegar a `done/dead` antes de solicitar uma nova execução para a mesma
versão/pipeline.
