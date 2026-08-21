# Vocabulário Semântico Regulatório — InteliReg

## Objetivo

O vocabulário semântico é um artefato de domínio controlado usado pelo
`rag-service` para reduzir a dependência de coincidência lexical literal.

Ele **não contém regras regulatórias, decisões de vigência ou pareceres**.
Seu papel é relacionar formas linguísticas equivalentes ou próximas, por
exemplo:

```text
harmonização farmacêutica
        ↓
ICH
International Council for Harmonisation
Assembleia ICH
Comitê Gestor ICH
```

O arquivo canônico é:

```text
config/semantic_vocabulary.json
```

O schema documental de referência fica em:

```text
config/semantic_vocabulary.schema.json
```

## Governança

A manutenção do vocabulário deve ser realizada por especialistas de domínio ou
revisada por eles.

Cada alteração deve:

1. possuir `id` estável para o conceito;
2. atualizar `vocabulary_version`;
3. registrar aliases apenas quando houver equivalência/associação semântica
   suficientemente segura para retrieval;
4. evitar incluir conclusões de aplicabilidade, vigência ou prevalência;
5. passar pela suíte `golden`;
6. ser revisada em pull request.

Após publicar uma nova versão do arquivo, reinicie os processos da API/worker
para que a versão cacheada seja substituída de maneira controlada.

O vocabulário é um auxílio de recuperação. A presença de um conceito em um
trecho não transforma o trecho em evidência suficiente para uma conclusão
regulatória.

## Estrutura de um conceito

```json
{
  "id": "ich_harmonizacao",
  "label": "ICH e harmonização farmacêutica internacional",
  "enabled": true,
  "aliases": [
    "ICH",
    "harmonização farmacêutica"
  ],
  "query_expansions": [
    "ICH",
    "International Council for Harmonisation",
    "Assembleia ICH"
  ],
  "embedding_terms": [
    "ICH",
    "harmonização farmacêutica internacional"
  ],
  "notes": "..."
}
```

### `aliases`

Expressões que identificam o conceito na pergunta ou no texto documental.

O matching é determinístico, case-insensitive e accent-insensitive, com
fronteiras de palavra para evitar casos como `ICH` dentro de outra palavra.

Alterar aliases pode afetar tanto expansão de query quanto enriquecimento de
passages. Quando `SEMANTIC_PASSAGE_ENRICHMENT_ENABLED=true`, alteração de
aliases deve ser tratada como alteração do perfil de indexação.

### `query_expansions`

Termos adicionados **somente à representação usada pelo retrieval**, nunca à
pergunta original persistida/exibida.

Não exigem reindexação quando são a única parte alterada.

### `embedding_terms`

Rótulos curtos adicionados ao input vetorial de chunks que casam com o conceito.
O texto canônico do chunk, FTS, citações e snapshots não são modificados.

Alterar `embedding_terms` exige reindexação para que os vetores reflitam a nova
versão.

## Três canais semânticos

Com o vocabulário habilitado, o retrieval usa:

```text
pergunta original
    │
    ├── FTS original
    │
    ├── query expandida → embedding vetorial
    │
    └── concept lookup determinístico
              │
              ▼
        pool de candidatos
              │
              ▼
      RRF + reranking auditável
```

O FTS continua recebendo a pergunta original para não regredir consultas
literais que já funcionam bem.

O `concept lookup` serve como canal de recall controlado: se a pergunta é
reconhecida como `harmonização farmacêutica`, chunks que utilizam o alias
canônico `ICH` podem entrar no pool mesmo quando o embedding não generaliza bem.

## Enriquecimento de passages

Na indexação, somente o texto enviado ao modelo de embeddings é enriquecido.

Exemplo conceitual:

```text
Documento: Portaria ...
Tipo: prt
Conceitos: ICH; harmonização farmacêutica internacional
Trecho: <texto canônico do chunk>
```

No banco, `embedding_chunks.text` continua contendo apenas o texto canônico.
Portanto:

- citações não são contaminadas;
- conteúdo apresentado ao Portal permanece fiel à fonte;
- o enriquecimento é uma propriedade da representação vetorial.


## Manifesto do perfil de indexação

A migration `0003_semantic_index_profiles.sql` cria `index_profiles`.

A primeira indexação de uma combinação `PIPELINE_VERSION + embedding_model_id`
registra:

- se enriquecimento semântico de passages estava ativo;
- versão do vocabulário no momento da indexação;
- hash do **perfil de embedding** (aliases + embedding_terms).

O worker recusa misturar outro perfil semântico na mesma `PIPELINE_VERSION`.
Se aliases ou `embedding_terms` mudarem, use uma nova versão de pipeline.

Mudanças somente em `query_expansions` alteram o hash completo do vocabulário,
mas não o hash do perfil de embedding; por isso podem ser publicadas sem
reindexação.

## Versionamento e reindexação

O perfil semântico inicial utiliza:

```dotenv
PIPELINE_VERSION=mvp-v2-semantic-v1
```

Recomendação:

- mudou apenas `query_expansions`: incremente `vocabulary_version`; não é
  necessário reindexar;
- mudou `aliases` ou `embedding_terms`: incremente `vocabulary_version` **e**
  use uma nova `PIPELINE_VERSION`, então reindexe.

Isso evita que duas representações vetoriais diferentes sejam auditadas sob o
mesmo identificador de pipeline.

## Inspeção local

Validar o arquivo e mostrar resumo:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary
```

Testar expansão:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary   "Quais atos tratam de fóruns internacionais de harmonização farmacêutica?"
```

Simular enriquecimento de um passage:

```bash
PYTHONPATH=src python -m intelireg.cli.semantic_vocabulary   --title "Portaria de teste"   --doc-type prt   --passage "Representantes da Anvisa na Assembleia ICH."
```

## Reindexação sem recrawl

Não é necessário baixar novamente as normas. Nodes já persistidos são usados
para recriar chunks/embeddings no pipeline novo.

Dry-run:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v1
```

Enfileirar:

```bash
PYTHONPATH=src python -m intelireg.cli.enqueue_reindex_all   --pipeline-version mvp-v2-semantic-v1   --execute
```

Depois execute o worker até consumir a fila. É possível preparar esse pipeline
enquanto a API ainda consulta o pipeline anterior e trocar
`PIPELINE_VERSION` somente após a reindexação.

## Auditoria

`/v1/rag/query` passa a registrar em `retrieval.semantic_expansion`:

- versão e hash do vocabulário;
- conceitos detectados;
- aliases que dispararam;
- termos adicionados;
- query expandida usada no retrieval.

Cada resultado também pode trazer:

- `semantic_concept_coverage`;
- `semantic_vocabulary_score`;
- `semantic_concepts_matched`;
- `semantic_lookup_match`;
- `semantic_lookup_rank`.

Esses sinais são diagnósticos de retrieval, não confiança regulatória.
