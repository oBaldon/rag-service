# RAG-QUALITY-04A/B — Seed de vocabulário regulatório do MVP

## Escopo

Esta tranche transforma o vocabulário semântico governado em um catálogo inicial de domínio para os cinco processos do MVP:

1. registro de medicamento;
2. alteração pós-registro;
3. AFE;
4. CBPF;
5. pesquisa clínica.

O catálogo é um **seed técnico**. Todos os conceitos entram como
`pending_domain_review` até revisão por especialista regulatório.

Fontes oficiais referenciadas no JSON são usadas como base terminológica. Elas
não transformam o vocabulário em parecer regulatório nem substituem conferência
da legislação e orientação vigentes.

## Versões

```text
schema_version      = 3
vocabulary_version  = semantic-v2.0-mvp-seed
concept_count       = 60
pipeline recomendado = mvp-v2-semantic-v2
```

A inclusão de novos aliases e `embedding_terms` altera o perfil vetorial.
Portanto esta versão requer um pipeline novo e reindexação dos embeddings.

## Prioridades

- `P0`: conceito central para o MVP e prioridade de revisão;
- `P1`: conceito relevante, mas secundário para a primeira homologação;
- `P2`: reservado para expansões futuras.

O seed atual contém 40 conceitos P0 e 20 conceitos P1.

## Estrutura schema v3

Além dos campos já existentes, cada conceito possui:

```json
{
  "priority": "P0",
  "parent_concepts": ["alteracao_pos_registro"],
  "related_concepts": ["metodo_analitico"],
  "source_refs": ["ANVISA-POST-01"]
}
```

O topo do vocabulário contém `sources`, com `id`, `label`, `url`,
`retrieved_at` e notas.

### Hierarquia

`parent_concepts` representa relação taxonômica. O loader rejeita:

- referência para conceito inexistente;
- referência para conceito inativo/rejeitado a partir de conceito ativo;
- autorreferência;
- ciclo na hierarquia.

`related_concepts` representa associação não hierárquica e pode ser
bidirecional.

A hierarquia **não é usada automaticamente para inferir requisitos
regulatórios** e não altera a camada de aplicabilidade normativa.

## Matching e especificidade

Quando uma mesma pergunta ativa um conceito genérico e um mais específico, o
matching prioriza o alias mais longo/específico. `priority` é usada apenas como
desempate de conceitos com especificidade semelhante.

Exemplo:

```text
"mudança de método analítico"
       ↓
mudanca_metodo_analitico
metodo_analitico
```

Isso reduz o risco de a expansão genérica consumir todo o orçamento de termos
antes do conceito específico.

## Governança

Todos os 60 conceitos estão inicialmente em:

```text
review_status = pending_domain_review
owner_role    = especialista_regulatorio
```

Fluxo recomendado:

1. especialista revisa `label`, aliases, expansões e relações;
2. revisa a fonte terminológica referenciada;
3. executa os golden tests;
4. registra `reviewer_role`, `reviewed_at` e `change_ref`;
5. altera `review_status` para `approved`;
6. abre PR para revisão técnica.

Não deve ser usado `approved` para registrar apenas aprovação técnica de
software; o status representa revisão do domínio.

## Golden tests

### Vocabulário

```bash
PYTHONPATH=src python golden/check_semantic_vocabulary_quality.py
```

O seed contém 164 casos.

Critério mínimo:

- P0: 2 casos positivos + 1 negativo por conceito;
- P1: 1 positivo + 1 negativo por conceito.

Para detalhes:

```bash
PYTHONPATH=src python golden/check_semantic_vocabulary_quality.py --verbose
```

### Intenções do MVP contra a API

Foi adicionada uma segunda suíte:

```bash
python golden/check_mvp_retrieval_seed.py   --base-url http://127.0.0.1:8088
```

Ela possui 25 consultas, cinco por processo do MVP, e valida:

- conceitos esperados ativados pela query;
- índice retornando resultados;
- documentos esperados, quando esses forem futuramente homologados.

Nesta versão `expected_documents` permanece vazio de propósito. A seleção dos
documentos corretos deve ser feita com especialistas, e não inferida pelo seed
técnico.

## Reindexação

Como o perfil de embedding mudou:

```dotenv
PIPELINE_VERSION=mvp-v2-semantic-v2
```

Dry-run:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v2
```

Enqueue:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v2   --execute
```

O enfileirador é idempotente. Não o execute novamente sem necessidade enquanto
o lote estiver ativo, embora a restrição de banco impeça duplicação ativa.

Depois de concluir a fila, faça o cutover da API para o novo pipeline e execute:

```bash
python golden/check_retrieval_quality.py   --base-url http://127.0.0.1:8088

python golden/check_mvp_retrieval_seed.py   --base-url http://127.0.0.1:8088
```

A suíte T01–T09 continua sendo a regressão obrigatória do retrieval já
estabilizado.

## Inspeção do catálogo

Resumo geral:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary
```

Apenas P0:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary --priority P0
```

Por processo:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary   --process pesquisa_clinica
```

Incluir fontes:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary --sources
```

## Limite do seed

Este catálogo organiza linguagem e recuperação. Ele não:

- afirma que uma norma está vigente;
- decide qual ato prevalece;
- determina obrigação regulatória;
- substitui a camada curada de aplicabilidade;
- substitui revisão humana especializada.
