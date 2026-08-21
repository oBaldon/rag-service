# Aplicabilidade Regulatória Curada — InteliReg

## Objetivo

A camada de aplicabilidade complementa o retrieval semântico com metadados
**curados e revisados** sobre situação e relações entre atos normativos.

Ela existe para separar duas perguntas diferentes:

1. **relevância** — o texto recuperado fala sobre o assunto?
2. **aplicabilidade** — qual é a situação conhecida do ato e como ele se
   relaciona com outros atos?

O RAG continua responsável pela primeira. A segunda não deve ser inferida
automaticamente pelo LLM.

## Modelo

A migration `0005_regulatory_applicability.sql` cria:

- `regulatory_status_assertions`;
- `regulatory_relations`.

### Situação normativa

Valores internos suportados inicialmente:

- `vigente`;
- `parcialmente_vigente`;
- `revogada`;
- `suspensa`;
- `substituida`;
- `sem_efeito`.

Ausência de assertion aprovada é retornada como:

```text
status = unknown
basis = no_approved_curated_assertion
```

`unknown` **não significa** que a autoridade considera a situação desconhecida.
Significa apenas que o InteliReg ainda não possui uma assertion curada e
aprovada para aquele documento.

### Relações

A direção é sempre:

```text
source_document --relation_type--> target_document
```

Tipos iniciais:

- `altera`;
- `revoga`;
- `revoga_parcialmente`;
- `substitui`;
- `regulamenta`;
- `complementa`;
- `prorroga`;
- `corrige`;
- `referencia`.

As relações inversas não são persistidas como outro fato. Ao consultar o
documento alvo, a API apresenta a mesma relação com `direction=inbound`.

## Governança

Cada assertion/relação possui:

- chave estável de curadoria (`assertion_key` / `relation_key`);
- `review_status`: `draft`, `approved` ou `rejected`;
- ator que registrou (`asserted_by`);
- revisor e data quando aprovada;
- evidência por `source_url`, `evidence_version_id` e/ou `evidence_note`.

O banco recusa um registro `approved` sem revisor/data e sem alguma forma de
evidência.

Somente registros `approved` enriquecem respostas RAG.

## Importação

Não há endpoint público de escrita. A curadoria inicial é feita por arquivo
JSON + CLI, reduzindo a superfície de alteração acidental.

Schema:

```text
config/regulatory_applicability.schema.json
```

Modelo vazio:

```text
config/regulatory_applicability.example.json
```

Dry-run:

```bash
PYTHONPATH=src python -m intelireg.cli.regulatory_applicability \
  --file caminho/lote.json
```

Persistir:

```bash
PYTHONPATH=src python -m intelireg.cli.regulatory_applicability \
  --file caminho/lote.json \
  --execute
```

O import valida UUIDs de documentos/versões antes de qualquer escrita e faz
upsert transacional pelas chaves de curadoria.

## Efeito no retrieval

Por padrão:

```dotenv
REGULATORY_APPLICABILITY_ENABLED=true
REGULATORY_APPLICABILITY_MAX_RELATIONS_PER_DOCUMENT=20
```

A camada é aplicada **depois do ranking**. Ela não adiciona boost, penalidade
ou filtro de vigência. Portanto, a baseline T01–T09 não deve mudar apenas pela
presença desses dados.

Cada `document` retornado passa a poder conter:

```json
{
  "regulatory_context": {
    "status": {
      "status": "unknown",
      "basis": "no_approved_curated_assertion"
    },
    "relations": []
  }
}
```

Quando houver curadoria aprovada, a resposta inclui sua proveniência.

## Limite de uso

Esses metadados são apoio à análise regulatória e devem permanecer sujeitos à
revisão humana especializada. Não constituem parecer da autoridade e não
autorizam o LLM a decidir automaticamente qual norma prevalece em um caso
concreto.
