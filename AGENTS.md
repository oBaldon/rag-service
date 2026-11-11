# AGENTS.md — RAG Service

## 1. Contexto e instruções compartilhadas
Este diretório é o repositório Git do serviço de recuperação/indexação documental. Repositórios irmãos esperados: `../intelireg/` (Portal) e `../bulario-service/` (produtor de bulas). `../intelireg-engineering/` é uma **pasta local de documentação, sem Git por enquanto**; não presuma branch, commit, remoto nem exija `git status` nela.

Para tarefas de arquitetura ou integração, consulte, se estiver disponível, `../intelireg-engineering/AGENTS.md`, a documentação pertinente e os `AGENTS.md` dos serviços envolvidos. Um workspace VS Code com múltiplas raízes não assegura carregamento automático das instruções dos diretórios irmãos. Se arquivos não existirem ou não forem acessíveis, explicite a limitação; siga as regras essenciais deste arquivo.

## 2. Fontes e verificação
- O checkout local já contém `README.md` com operação Docker. Comece por ele e consulte `system_requirements.md`, `api/guia_uso_api_rag_intelireg.md`, `docs/api_contract_v1.md`, `docs/schema_query_v1.md`, `golden/README.md`, `.env.example`, migrations e testes pertinentes.
- Neste repositório Git, identifique branch, HEAD, diferenças locais e código efetivo antes de sustentar um diagnóstico: `git status --short --branch` e `git rev-parse HEAD`.
- Não trate documentos de planejamento, comandos descritos ou arquivos existentes como evidência de deploy, migration aplicada, índice íntegro ou worker ativo. Indique evidências e lacunas.

## 3. Arquitetura e contratos
- A API FastAPI integra o Portal por `POST /v1/rag/query`. Verifique payloads, erros, autenticação, proveniência, `request_id`/`run_id` e endpoints de saúde antes de modificar contratos. O fluxo extrativo `/v1/rag/ask` não equivale à geração de resposta LLM feita pelo Portal.
- A implementação examinada usa `sentence-transformers` com `intfloat/multilingual-e5-small`, embeddings de 384 dimensões e PostgreSQL/pgvector. Não pressuponha embeddings via Ollama; alterações de modelo/dimensão exigem avaliação e plano de compatibilidade.
- API e consumidor de jobs de indexação são processos distintos. Preserve pipeline, idempotência, perfis de recuperação e baseline de avaliação. O schema previsto é `intelireg`; `PG_SCHEMA` isoladamente não garante `search_path` efetivo por conexão: confira `src/intelireg/db.py` e consultas SQL.
- Se alterar APIs, URLs internas ou autenticação, confronte `RagClient`/`HttpRagClient`, configuração e testes de integração em `../intelireg/`.

## 4. Dockerização: implementação local e verificação por ambiente
- Dockerfile e Compose independente já foram implementados localmente, com API/worker separados, rede externa e PostgreSQL compartilhado. Confira código, README e estado do ambiente antes de afirmar deploy no servidor principal; não confunda validação local com homologação de produção.
- Planeje imagem Python, dependências de embeddings, cache persistente, volumes/permissões, configuração por ambiente, healthcheck/readiness e autenticação sem vazar segredos.
- O Compose atual do Portal usa `pgvector/pgvector:0.8.6-pg16-bookworm`; o snapshot antigo usava Alpine. A configuração do checkout não comprova o servidor principal. Verifique extensão e compatibilidade da imagem/volume real; documente backup e rollback antes de mudanças no banco.
- A configuração `RAG_SERVICE_URL` do Portal deve apontar para o endpoint completo `/v1/rag/query`; revise também endpoint de saúde e chave/autenticação.
- Separe API, worker, migrations/bootstrap e ingestão/reindexação. Não utilize `golden/01_update_kb.sh` como entrypoint de inicialização: seus efeitos precisam de auditoria, pois pode executar reset ou reindexação.
- Trate a dockerização como alteração de infraestrutura: não mude simultaneamente algoritmo de recuperação, modelo de embedding e contrato da API sem escopo aprovado.

## 5. Segurança, validação e entregas
- Padrão: inspeção somente leitura, sem editar código nem iniciar migrations, reset, bootstrap, ingestão, reindexação, jobs ou chamadas operacionais sem autorização explícita e identificação do ambiente-alvo.
- Não exponha `DATABASE_URL`, `PG_SUPERUSER_URL`, `RAG_API_KEY`, `HF_TOKEN` nem conteúdo sensível de `.env`. Investigue efeitos de testes antes de executá-los.
- Proponha patches incrementais com arquivos afetados, dependências dos outros serviços, testes de contrato/recuperação, preservação de dados e plano de rollback. Compare a baseline de recuperação antes/depois quando houver validação autorizada.
- Registre decisões aprovadas em documentação local de `../intelireg-engineering/` mediante autorização; não presuma que ela está versionada. Diferencie proposta, patch aplicado e resultado de teste efetivamente observado.

## Ambiente local durante desenvolvimento
- Ao alterar `.env.example`, sincronize também o `.env` desta execução local com valores corretos para o ambiente identificado, preservando segredos e configurações existentes. Nunca copie credenciais reais para o exemplo, logs ou relatórios. Esta atualização local foi explicitamente solicitada pelo usuário.

## Recuperação documental
- Para resumos, conferir `docs/full-document-contract.md`, `src/intelireg/full_document.py` e `../intelireg/docs/NORM_SUMMARY.md`. `/v1/rag/document` lê todos os nós, não usa top-k nem gera resumo; `run_id` não é persistido em `rag_runs`. Integridade do hash não certifica extração fiel ao original.
