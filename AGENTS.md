# AGENTS.md — RAG Service

## 1. Contexto e instruções compartilhadas
Este diretório é o repositório Git do serviço de recuperação/indexação documental. Repositórios irmãos esperados: `../intelireg/`, `../bulario-service/` e `../intelireg-engineering/`, responsável pela documentação de engenharia. Verifique acesso e metadados Git antes de atribuir revisões; não presuma sincronização com remoto.

Para tarefas de arquitetura ou integração, consulte, se estiver disponível, `../intelireg-engineering/AGENTS.md`, a documentação pertinente e os `AGENTS.md` dos serviços envolvidos. Um workspace VS Code com múltiplas raízes não assegura carregamento automático das instruções dos diretórios irmãos. Se arquivos não existirem ou não forem acessíveis, explicite a limitação; siga as regras essenciais deste arquivo.

## 2. Fontes e verificação
- O checkout local já contém `README.md` com operação Docker. Comece por ele e consulte `system_requirements.md`, `api/guia_uso_api_rag_intelireg.md`, `docs/api_contract_v1.md`, `docs/schema_query_v1.md`, `golden/README.md`, `.env.example`, migrations e testes pertinentes.
- Neste repositório Git, identifique branch, HEAD, diferenças locais e código efetivo antes de sustentar um diagnóstico: `git status --short --branch` e `git rev-parse HEAD`.
- Não trate documentos de planejamento, comandos descritos ou arquivos existentes como evidência de deploy, migration aplicada, índice íntegro ou worker ativo. Indique evidências e lacunas.

## 3. Arquitetura e contratos
- A API FastAPI integra o Portal por `POST /v1/rag/query`. Verifique payloads, erros, autenticação, proveniência, `request_id`/`run_id` e endpoints de saúde antes de modificar contratos. O fluxo extrativo `/v1/rag/ask` não equivale à geração de resposta LLM feita pelo Portal.
- Confira o provedor, modelo, dimensão e pipeline em `src/intelireg/embeddings.py`, `src/intelireg/settings.py` e nos perfis do índice. Não pressuponha embeddings via Ollama; preserve compatibilidade com os dados indexados e avalie necessidade de migração antes de propor mudanças.
- API e consumidor de jobs de indexação são processos distintos. Preserve pipeline, idempotência, perfis de recuperação e baseline de avaliação. O schema previsto é `intelireg`; `PG_SCHEMA` isoladamente não garante `search_path` efetivo por conexão: confira `src/intelireg/db.py` e consultas SQL.
- Se alterar APIs, URLs internas ou autenticação, confronte os consumidores pertinentes no Portal: `RagClient`/`HttpRagClient`, `NormDocumentClient`/`HttpNormDocumentClient`, configuração e testes de contrato.

## 4. Docker e verificação por ambiente
- Preserve a separação de API e worker e confira Dockerfile, Compose, README e evidências do ambiente antes de recomendar comandos. Configuração do checkout e validação local não comprovam implantação ou homologação em outro ambiente.
- Planeje imagem Python, dependências de embeddings, cache persistente, volumes/permissões, configuração por ambiente, healthcheck/readiness e autenticação sem vazar segredos.
- Para imagem PostgreSQL, extensão pgvector, volumes e transição de instalações, consulte o Compose efetivo, `../intelireg/docs/RAG_DEPLOYMENT_TRANSITION.md` e a documentação pertinente em `../intelireg-engineering/infrastructure/`. Não troque imagem ou volume com base apenas em referências históricas.
- A configuração `RAG_SERVICE_URL` do Portal deve apontar para o endpoint completo `/v1/rag/query`; revise também endpoint de saúde e chave/autenticação.
- Separe API, worker, migrations/bootstrap e ingestão/reindexação. Não utilize `golden/01_update_kb.sh` como entrypoint de inicialização: seus efeitos precisam de auditoria, pois pode executar reset ou reindexação.
- Trate a dockerização como alteração de infraestrutura: não mude simultaneamente algoritmo de recuperação, modelo de embedding e contrato da API sem escopo aprovado.

## 5. Segurança, validação e entregas
- Aplique o controle de escopo e autorização deste arquivo. Bootstrap, ingestão, reindexação e consumo de jobs são operações administrativas separadas da inicialização da API.
- Não classifique consultas como somente leitura apenas por recuperarem documentos: `/v1/rag/query` e `/v1/rag/ask` podem persistir auditoria em `rag_runs`. O contrato `/v1/rag/document` não persiste essa auditoria; examine a implementação vigente e os demais efeitos antes da execução.
- Não exponha `DATABASE_URL`, `PG_SUPERUSER_URL`, `RAG_API_KEY`, `HF_TOKEN` nem conteúdo sensível de `.env`. Investigue efeitos de testes antes de executá-los.
- Proponha patches incrementais com arquivos afetados, dependências dos outros serviços, testes de contrato/recuperação, preservação de dados e plano de rollback. Compare a baseline de recuperação antes/depois quando houver validação autorizada.
- Identifique os contratos e registros de engenharia afetados e atualize-os somente no escopo autorizado. Diferencie proposta, patch aplicado e resultado de teste efetivamente observado.

## Ambiente local durante desenvolvimento
- Quando uma implementação autorizada alterar `.env.example`, sincronize também as variáveis correspondentes no `.env` apenas do ambiente local de desenvolvimento identificado, preservando segredos e demais configurações. Não substitua o arquivo inteiro nem propague valores entre ambientes. Outros ambientes, inclusive testes compartilhados, homologação e produção, exigem autorização específica. Se o alvo ou os valores necessários não puderem ser determinados, informe a pendência antes de alterar essa configuração. Nunca copie credenciais reais para exemplos, logs ou relatórios.

## Recuperação documental
- Para resumos, conferir `docs/full-document-contract.md`, `src/intelireg/full_document.py` e `../intelireg/docs/NORM_SUMMARY.md`. `/v1/rag/document` lê todos os nós, não usa top-k nem gera resumo; `run_id` não é persistido em `rag_runs`. Integridade do hash não certifica extração fiel ao original.

## Validação específica do RAG
- Use `pytest.ini` e selecione testes pertinentes de contratos, autenticação, recuperação documental, search_path, perfis e idempotência. Confira dependências em `requirements-dev.txt`; para alterações de imagem, examine também Dockerfile, `requirements-docker.lock` e `docker/constraints.in`.
- Testes com banco ou embeddings simulados não comprovam inferência, recuperação real, integridade do corpus ou consumo do worker. Esses aceites exigem ambiente e corpus identificados e autorização compatível com seus efeitos.
- Para mudanças em recuperação, ranking, vocabulário ou embeddings, proponha comparação da baseline relevante em `golden/`; não execute atualização de KB ou reset como teste de regressão. Examine também exposição de configuração nos scripts antes de executá-los.

## Controle de escopo, autorização e compatibilidade
- Uma solicitação de análise ou planejamento autoriza somente leitura e propostas. Uma solicitação explícita de implementação autoriza as edições e verificações isoladas necessárias dentro do escopo indicado; não exige nova aprovação para cada edição já abrangida. Permissões das ferramentas não ampliam a autorização do usuário.
- Preserve a arquitetura existente e faça apenas as alterações necessárias. Não inclua refatorações, otimizações, ajustes estéticos, novos padrões, abstrações, componentes ou dependências sem necessidade demonstrada para o requisito autorizado.
- Considere estruturais as mudanças de responsabilidade entre serviços, contratos públicos, schemas compartilhados, formatos persistidos, semântica de filas, topologia de execução, persistência, modelo/dimensão de embeddings ou introdução de componentes e dependências de produção. Antes de implementá-las, apresente alternativas pertinentes, impactos, compatibilidade, validação e reversão; obtenha aprovação específica se ela ainda não estiver contemplada na solicitação.
- Antes de alterar um contrato compartilhado, identifique produtores e consumidores e examine código, documentação e testes dos envolvidos. Preserve interfaces, dados históricos e integrações; mudanças incompatíveis exigem autorização explícita e plano de transição. Se faltar evidência indispensável, suspenda apenas a parte dependente e informe a limitação.
- Operações que alterem bancos, dados persistentes, volumes, migrations aplicadas, infraestrutura, credenciais ou ambientes em execução exigem autorização específica e identificação do alvo. Para operações destrutivas ou migrações de dados, apresente também backup e reversão apropriados. Criar commits ou fazer push exige autorização própria.
- Antes de qualquer acesso de rede, verifique a autorização vigente. Consultas a documentação pública devem limitar-se à leitura necessária ao escopo, sem enviar conteúdo restrito; consultar documentação não autoriza executar os endpoints descritos. Acessos a APIs reais, serviços internos ou bancos remotos exigem autorização específica para alvo, finalidade e efeitos, inclusive em consultas. Transmissão de dados sensíveis exige autorização explícita e canal apropriado. Não amplie permissões operacionais implicitamente.
- Antes de executar uma verificação, examine seus efeitos sobre arquivos, rede, serviços, banco e storage. Diferencie testes isolados de integração real e operação administrativa. Execute somente a validação proporcional autorizada e reporte resultados observados, limitações e verificações não realizadas.
- Fundamente mudanças em caminhos, símbolos, contratos e revisões verificáveis. Não enfraqueça validações, rastreabilidade ou testes para acomodar uma implementação. Mantenha nomes de volumes, configurações de servidores, histórico de migrações e estado operacional na documentação técnica existente, referenciada conforme a tarefa.
