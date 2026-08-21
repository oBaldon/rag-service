# Relatório de incidente: reindexação semântica duplicada

**Data:** 20 de agosto de 2026

**Serviço:** `rag-service`

**Componente:** fila `IndexVersionJob` / `index_worker`

**Pipeline afetado:** `mvp-v2-semantic-v1`

**Status:** contido e dados validados; prevenção de recorrência pendente

## Resumo executivo

O corpus de 3.224 versões foi enfileirado duas vezes para o pipeline
`mvp-v2-semantic-v1`. O primeiro lote concluiu a reindexação corretamente. O
segundo lote começou a processar as mesmas versões com `force=true`, apagando e
recriando os chunks e embeddings V2 já existentes.

O incidente não criou registros duplicados nem causou perda confirmada de
dados. Ele provocou reprocessamento desnecessário, consumo de CPU/GPU e uma
aparente continuidade da chunkerização mesmo depois de V1 e V2 atingirem a
mesma quantidade de embeddings.

O worker foi interrompido durante o job `7248`. Os 2.425 jobs ainda ativos do
segundo lote foram cancelados de forma auditável e a integridade dos dois
pipelines foi confirmada.

## Sintoma observado

A consulta de contagem retornou 19.314 embeddings em cada pipeline:

| Pipeline | Embeddings |
|---|---:|
| `mvp-v1` | 19.314 |
| `mvp-v2-semantic-v1` | 19.314 |

Apesar da paridade, o `index_worker` continuou emitindo mensagens como:

```text
[index_worker] done job_id=... pipeline=mvp-v2-semantic-v1 chunks=...
```

Essa consulta comparava apenas quantidades. Ela não demonstrava que os vetores
eram iguais e também não informava se ainda havia jobs redundantes na fila.

## Escopo e cronologia dos lotes

Foram identificados exatamente três lotes de jobs, e não quatro:

| Lote | Intervalo de jobs | Quantidade | Resultado |
|---|---:|---:|---|
| V1 original | `1–3224` | 3.224 | concluído |
| primeiro V2 | `3225–6448` | 3.224 | concluído |
| segundo V2 redundante | `6449–9672` | 3.224 | parcialmente executado e cancelado |

Cada uma das 3.224 versões possuía um job V1 e dois jobs V2. Portanto, havia
uma repetição indevida por versão, não duas.

No segundo lote V2:

- 799 jobs (`6449–7247`) terminaram antes da interrupção;
- o job `7248` foi interrompido durante a geração de embeddings;
- 2.425 jobs (`7248–9672`) ainda estavam em estado ativo e foram cancelados.

Após a contenção, os jobs V2 ficaram distribuídos assim:

| Estado | Quantidade | Composição |
|---|---:|---|
| `done` | 4.023 | 3.224 do primeiro lote + 799 reprocessamentos |
| `dead` | 2.425 | restante redundante cancelado |
| `queued` / `running` / `failed` | 0 | nenhuma atividade pendente |

## Causa raiz

A seleção feita por `src/intelireg/cli/enqueue_reindex_all.py` considera uma
versão elegível quando ainda não existem chunks no pipeline de destino:

```sql
NOT EXISTS (
  SELECT 1
  FROM embedding_chunks c
  WHERE c.version_id = v.version_id
    AND c.pipeline_version = %s
)
```

Ela não verifica se já existe um `IndexVersionJob` `queued`, `running` ou
`failed` para a mesma combinação de `version_id` e `pipeline_version`.

Assim, uma segunda execução do enfileirador, realizada antes de o primeiro lote
produzir chunks, voltou a considerar as 3.224 versões como ausentes e criou um
segundo lote completo. A função genérica `enqueue_job` também não possui chave
de idempotência ou restrição de unicidade para impedir essa repetição.

Todos esses jobs foram criados com `force=true`. No worker, esse modo permite
reindexar versões com status `INDEXED`. O processamento remove os chunks e
embeddings do mesmo pipeline e os recria dentro de uma transação. Por isso a
contagem total permaneceu estável enquanto o segundo lote continuava sendo
processado.

## Impacto

### Impacto confirmado

- reprocessamento completo ou parcial de versões já indexadas;
- consumo desnecessário de CPU/GPU durante a geração de embeddings;
- aumento do tempo operacional e do volume de logs;
- 799 versões V2 foram efetivamente processadas uma segunda vez;
- necessidade de intervenção manual para interromper e limpar a fila.

### Impacto não observado

- não houve aumento indevido da quantidade de chunks;
- não foram encontrados chunks duplicados armazenados;
- não houve perda confirmada de chunks ou embeddings válidos;
- não ficaram jobs V2 ativos após a contenção;
- não foram encontrados vetores ou índices FTS ausentes.

A ausência de duplicação armazenada é explicada pela remoção e reinserção
transacional e pela restrição única de chunks por versão, pipeline e hash.

## Verificações realizadas

### Paridade de cobertura

Após a contenção:

| Pipeline | Versões | Chunks | Embeddings | FTS vazio |
|---|---:|---:|---:|---:|
| `mvp-v1` | 3.224 | 19.314 | 19.314 | 0 |
| `mvp-v2-semantic-v1` | 3.224 | 19.314 | 19.314 | 0 |

Todas as versões possuíam a mesma quantidade de chunks nos dois pipelines.
Os 19.314 pares de chunks também apresentaram texto canônico e tamanho iguais,
o que é esperado porque a alteração semântica enriquece somente o texto de
entrada do embedding e não modifica o texto canônico do chunk.

### Comparação dos vetores

A igualdade das contagens não significava igualdade dos embeddings. A
comparação dos 19.314 pares mostrou:

- embeddings exatamente iguais: 0;
- distância cosseno média: `0,01560264`;
- distância cosseno mínima: `0,00275546`;
- distância cosseno máxima: `0,12412992`.

Isso confirma que o pipeline semântico produziu vetores diferentes, apesar de
preservar a mesma chunkerização.

### Segurança da interrupção

O `Ctrl+C` ocorreu dentro de `model.encode`, antes do commit do job `7248`.
Como a exclusão e a recriação dos chunks acontecem na mesma transação, a
interrupção causou rollback do trabalho incompleto. O conjunto V2 previamente
gravado permaneceu disponível.

Também foi confirmado que não havia outro processo `index_worker` ativo antes
do cancelamento da fila.

## Correção aplicada

1. O `index_worker` foi interrompido com `Ctrl+C`.
2. Foi verificado que todas as versões dos jobs ativos já possuíam chunks no
   pipeline `mvp-v2-semantic-v1`.
3. Somente os jobs ativos do segundo lote, limitados ao intervalo
   `6449–9672`, foram selecionados para cancelamento.
4. Os 2.425 jobs encontrados foram marcados como `dead`.
5. `locked_at` e `locked_by` foram limpos.
6. O motivo `segundo lote redundante; versão já indexada no pipeline de
   destino` foi registrado em `last_error` para preservar auditoria.
7. A fila e a integridade de chunks, embeddings e FTS foram verificadas
   novamente.

Nenhum chunk, embedding ou job concluído foi excluído durante a correção.

## Prevenção de recorrência pendente

A contenção operacional foi concluída, mas a causa no enfileirador ainda deve
ser corrigida. Recomenda-se:

1. Fazer `enqueue_reindex_all` excluir versões que já possuam job ativo para o
   mesmo `version_id` e `pipeline_version`.
2. Adicionar idempotência no banco, preferencialmente por uma chave explícita
   ou restrição única aplicável a jobs ativos.
3. Executar a seleção e o enfileiramento na mesma transação, usando conflito
   como operação sem efeito quando o job já existir.
4. Exibir separadamente no dry-run as quantidades `missing_pipeline`,
   `already_queued` e `eligible_to_enqueue`.
5. Adicionar teste automatizado que execute o enfileirador duas vezes antes do
   worker e confirme que a segunda execução cria zero jobs.
6. Monitorar jobs ativos agrupados por `version_id` e `pipeline_version`,
   alertando quando houver multiplicidade maior que um.

Até essa correção ser implementada, o comando de reindexação não deve ser
executado novamente enquanto houver jobs ativos para o mesmo pipeline.
