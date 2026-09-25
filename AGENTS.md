# AGENTS.md — RAG Service

## 1. Contexto e instruções compartilhadas
Este diretório é o repositório Git do serviço de recuperação/indexação documental. Repositórios irmãos esperados: `../intelireg/` (Portal) e `../bulario-service/` (produtor de bulas). `../intelireg-engineering/` é uma **pasta local de documentação, sem Git por enquanto**; não presuma branch, commit, remoto nem exija `git status` nela.

Para tarefas de arquitetura ou integração, consulte, se estiver disponível, `../intelireg-engineering/AGENTS.md`, a documentação pertinente e os `AGENTS.md` dos serviços envolvidos. Um workspace VS Code com múltiplas raízes não assegura carregamento automático das instruções dos diretórios irmãos. Se arquivos não existirem ou não forem acessíveis, explicite a limitação; siga as regras essenciais deste arquivo.

## 2. Fontes e verificação
- A cópia examinada não apresentava `README.md` na raiz. Verifique a versão local e consulte `system_requirements.md`, `api/guia_uso_api_rag_intelireg.md`, `docs/api_contract_v1.md`, `docs/schema_query_v1.md`, `golden/README.md`, `.env.example`, migrations e testes pertinentes.
- Neste repositório Git, identifique branch, HEAD, diferenças locais e código efetivo antes de sustentar um diagnóstico: `git status --short --branch` e `git rev-parse HEAD`.
- Não trate documentos de planejamento, comandos descritos ou arquivos existentes como evidência de deploy, migration aplicada, índice íntegro ou worker ativo. Indique evidências e lacunas.

## 3. Arquitetura e contratos
- A API FastAPI integra o Portal por `POST /v1/rag/query`. Verifique payloads, erros, autenticação, proveniência, `request_id`/`run_id` e endpoints de saúde antes de modificar contratos. O fluxo extrativo `/v1/rag/ask` não equivale à geração de resposta LLM feita pelo Portal.
- A implementação examinada usa `sentence-transformers` com `intfloat/multilingual-e5-small`, embeddings de 384 dimensões e PostgreSQL/pgvector. Não pressuponha embeddings via Ollama; alterações de modelo/dimensão exigem avaliação e plano de compatibilidade.
- API e consumidor de jobs de indexação são processos distintos. Preserve pipeline, idempotência, perfis de recuperação e baseline de avaliação. O schema previsto é `intelireg`; `PG_SCHEMA` isoladamente não garante `search_path` efetivo por conexão: confira `src/intelireg/db.py` e consultas SQL.
- Se alterar APIs, URLs internas ou autenticação, confronte `RagClient`/`HttpRagClient`, configuração e testes de integração em `../intelireg/`.

## 4. Dockerização: evolução planejada, não presumida como concluída
- A próxima sprint prevê Dockerizar API e worker em serviços separados, reutilizando rede e PostgreSQL compartilhados. Confirme os arquivos locais antes de afirmar que a dockerização continua pendente.
- Planeje imagem Python, dependências de embeddings, cache persistente, volumes/permissões, configuração por ambiente, healthcheck/readiness e autenticação sem vazar segredos.
- A imagem PostgreSQL do Portal examinada era `postgres:16-alpine`: **não presuma `pgvector` presente**. Verifique extensão e compatibilidade da imagem/volume real; documente backup e rollback antes de mudanças no banco.
- A configuração `RAG_SERVICE_URL` do Portal deve apontar para o endpoint completo `/v1/rag/query`; revise também endpoint de saúde e chave/autenticação.
- Separe API, worker, migrations/bootstrap e ingestão/reindexação. Não utilize `golden/01_update_kb.sh` como entrypoint de inicialização: seus efeitos precisam de auditoria, pois pode executar reset ou reindexação.
- Trate a dockerização como alteração de infraestrutura: não mude simultaneamente algoritmo de recuperação, modelo de embedding e contrato da API sem escopo aprovado.

## 5. Segurança, validação e entregas
- Padrão: inspeção somente leitura, sem editar código nem iniciar migrations, reset, bootstrap, ingestão, reindexação, jobs ou chamadas operacionais sem autorização explícita e identificação do ambiente-alvo.
- Não exponha `DATABASE_URL`, `PG_SUPERUSER_URL`, `RAG_API_KEY`, `HF_TOKEN` nem conteúdo sensível de `.env`. Investigue efeitos de testes antes de executá-los.
- Proponha patches incrementais com arquivos afetados, dependências dos outros serviços, testes de contrato/recuperação, preservação de dados e plano de rollback. Compare a baseline de recuperação antes/depois quando houver validação autorizada.
- Registre decisões aprovadas em documentação local de `../intelireg-engineering/` mediante autorização; não presuma que ela está versionada. Diferencie proposta, patch aplicado e resultado de teste efetivamente observado.
