# Governança do Vocabulário Semântico

## Papel

O vocabulário é um ativo de domínio do InteliReg. Ele traduz termos usados por
especialistas e usuários em conceitos estáveis usados pelo retrieval.

A partir do schema v3 cada conceito possui, além de aliases e termos de
expansão:

- `priority` (`P0`, `P1`, `P2`);
- `domains`;
- `parent_concepts`;
- `related_concepts`;
- `source_refs`;
- `regulatory_processes`;
- `governance.lifecycle`;
- `governance.review_status`;
- `governance.owner_role`;
- `governance.reviewer_role`;
- `governance.reviewed_at`;
- `governance.change_ref`.

Processos regulatórios controlados nesta tranche:

- `registro_medicamento`;
- `pos_registro`;
- `afe`;
- `cbpf`;
- `pesquisa_clinica`;
- `transversal`.

## Estados de revisão

`pending_domain_review`
: conceito tecnicamente ativo, mas ainda pendente de homologação de domínio.

`approved`
: conceito revisado por especialista; exige `reviewer_role` e `reviewed_at`.

`rejected`
: não participa do retrieval.

`lifecycle=deprecated` também remove o conceito do retrieval sem apagar seu
histórico no arquivo.

O seed inicial do MVP contém 60 conceitos em `pending_domain_review`. Eles
foram validados tecnicamente pelas suítes de software, mas isso não equivale à
homologação regulatória por especialista.

## Colisões

O loader bloqueia aliases idênticos entre conceitos ativos usando a mesma
normalização case/accent-insensitive usada pelo matching.

Isso evita que uma expressão única ative silenciosamente dois conceitos
distintos.

## Golden tests

O arquivo:

```text
golden/semantic_vocabulary_cases.json
```

mantém casos positivos e negativos por conceito.

Execute:

```bash
PYTHONPATH=src python golden/check_semantic_vocabulary_quality.py
```

O check falha se:

- a versão esperada divergir do vocabulário carregado;
- um caso positivo/negativo não tiver o comportamento esperado;
- um conceito P0 não possuir pelo menos 2 casos positivos e 1 negativo;
- um conceito P1 não possuir pelo menos 1 caso positivo e 1 negativo.

A suíte T01–T09 continua sendo o benchmark de retrieval end-to-end.

## Versionamento

Metadados de governança, `priority`, `domains`, `regulatory_processes`,
`parent_concepts`, `related_concepts`, `source_refs` e `query_expansions` não
alteram o perfil vetorial já indexado.

`aliases` e `embedding_terms` continuam compondo
`semantic_embedding_profile_hash`; alterá-los exige nova `PIPELINE_VERSION` e
reindexação.

Por isso é possível homologar um conceito pendente, alterando somente seu
`review_status`, sem reindexar o corpus.


## Fontes terminológicas

O schema v3 mantém uma lista top-level `sources`. Cada conceito ativo deve
referenciar pelo menos uma fonte via `source_refs`.

Essas fontes justificam a escolha terminológica do seed e ajudam a revisão por
especialistas. Elas **não** são assertions de vigência nem de aplicabilidade.
Esses fatos permanecem na camada separada de `regulatory_applicability`.

## Hierarquia e relações

`parent_concepts` é uma relação taxonômica e não pode conter ciclos.
`related_concepts` é associativa e pode ser bidirecional.

Nenhuma dessas relações é convertida automaticamente em conclusão normativa.
