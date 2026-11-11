# Dockerização — análise e validação

Data: 07/11/2026. Status: implementação local; resultados abaixo não comprovam deploy.

## Revisões-base

- Portal: `237e6e4e79d4cdafd8e541b5cc2495f900a2b9f0`, main, limpo antes da mudança.
- RAG: `7751e0006df3cca64de143821237108ec15651d1`, main, limpo antes da mudança.
- Bulário: `c3a1ebacf248d77d9faa14a8bc521817ade3c145`, main, com alterações locais
  preexistentes em Dockerfile, README, extração, pipeline, observabilidade e testes.
  Nenhum desses arquivos foi modificado nesta atividade.
- Engenharia: pasta local sem Git; documentos de referência de 24/09/2026.

## Diagnóstico observado

Os quatro diretórios permitiram leitura e indicaram permissão de escrita; o daemon
Docker exigiu execução fora do sandbox. Inspeção somente leitura confirmou:

- Compose Portal `intelireg-local`, DB `pgvector/pgvector:0.8.6-pg16-bookworm`,
  PostgreSQL 16.15 ativo, porta interna 5432, rede `intelireg-local_default`.
- Volume ativo definido pelo Compose: `intelireg_pgvector_pgdata`; há volume
  legado `intelireg_pgdata` declarado, que não foi removido nem reutilizado.
- Bancos não-template: `postgres`, `intelireg`.
- Schemas de aplicação: `public`, `bulario`, `intelireg`.
- Extensões: `pgcrypto 1.3`, `unaccent 1.1`, `vector 0.8.6`.
- Fila `intelireg.jobs`: 3224 jobs `done`, sem outros estados na consulta;
  índice `uq_jobs_active_idempotency` presente. Isso não certifica todas as migrations.
- `public.jobs` também existe; `search_path` explícito por conexão é necessário.
- RAG não tinha Dockerfile, Compose ou lock; usa pip/requirements, não uv project.
- Bulário usa Compose próprio, rede externa e `db:5432`; publica `public.bulas`
  e mantém histórico em `bulario.*`. Não foi encontrada dependência direta do
  runtime RAG sobre o produtor ou seu acervo.
- O `start.sh` do Portal inicia banco, dependências/assets, app e workers Laravel;
  migrations/seed dependem de opções explícitas. O fluxo padrão foi preservado.

A documentação `intelireg-engineering/infrastructure/database-topology.md`,
`docker-topology.md` e ADR-002 ainda descreve PostgreSQL Alpine/volume antigo.
O código e a instância real prevaleceram na implementação. ADR-002 e sprint 001
propõem Compose RAG independente; essa topologia foi adotada sem editar engenharia.
Após revisão desta entrega, recomenda-se atualizar esses registros e o estado da
sprint mediante autorização específica para escrita naquela pasta.

## Resultados

- Compose Portal e RAG: configuração válida com `config --quiet`.
- Shell `start.sh`: `bash -n` passou.
- Suíte RAG no venv local: **86 testes passaram**, incluindo isolamento/quoting do
  schema e fechamento da conexão em falha. Banco configurado com destino inválido;
  dependências operacionais simuladas. Pytest instalado somente em `/tmp`.
- Build concluído: imagem `intelireg-rag:local`, ID
  inicial `sha256:6844ed6690f7714050f39d9f537404c887991d35f63bd2574ae3e59a5b744e98`.
  Rebuild final com digest fixo da mesma base passou usando as mesmas camadas;
  manifest final `sha256:9392ffbf20cfa044a42a0b799c5669d77f2a22eadf421b94d5ae55a3c66b9abc`.
  A instalação CPU exigiu instalar torch separadamente pelo índice oficial antes
  das demais dependências. Importação na imagem confirmou `torch 2.14.0+cpu`,
  CUDA ausente e `sentence-transformers 6.1.0`.
- Após aprovação explícita do usuário para execução local, somente `rag-api`
  foi iniciado e ficou healthy; worker não foi iniciado. Volume criado somente
  para cache HF. PostgreSQL e seus volumes não foram recriados.
- Dentro da imagem, UID 10001, banco/schema `intelireg`, search_path
  `intelireg, public`, mantido após rollback; `jobs` resolve para `intelireg.jobs`.
  Extensões confirmadas pela conexão efetiva da aplicação.
- Container Laravel existente (`queue-1`) → `rag-api:8088/health/ready`: HTTP 200,
  status ready. POST vazio sem credencial em `/v1/rag/query`: HTTP 401,
  sem executar consulta/auditoria. Não foi realizado teste de recuperação válida.
- Configuração de conexão Docker fornecida em memória na validação, sem alterar
  `.env`. Para usar `./start.sh --rag` posteriormente, persista
  `RAG_DOCKER_DATABASE_URL` no ambiente privado conforme README.
- O primeiro teste dentro da imagem encontrou fixture golden ausente (85 passaram,
  1 falhou); com o fixture JSON montado read-only, **86 testes passaram** na
  imagem, sem rede e com destino de banco inválido. Nenhum script golden executado.
  Um aviso de depreciação de httpx no TestClient foi emitido em ambas as suítes.

Durante a atividade, foi observado novo HEAD do Portal
`28df9454edbd9d9a1a52e6004203ae0b6c218bf0`, já incluindo start.sh e exemplo de
ambiente preparados nesta atividade. Nenhum commit foi executado pelo agente.
Alterações externas de Git foram preservadas.

## Limites de aceite

Não executar consulta RAG válida, worker, migrations, ingestão ou golden no corpus
existente para esta validação. Consultas válidas escrevem auditoria e o worker
consome jobs. Validar qualidade de embeddings/recuperação, continuidade de jobs e
rollback operacional em homologação antes de promover para produção.

## Ajuste do ambiente local e do consumidor — 07/11/2026

Após solicitação explícita de sincronização dos `.env` reais:

- Os dois ambientes têm todas as chaves dos respectivos exemplos. Valores locais
  foram preservados, com mudanças restritas à integração RAG e CPU.
- Portal usa `http://rag-api:8088/v1/rag/query` e
  `http://rag-api:8088/health/ready`; chave comum preservada nos dois serviços.
- RAG com autenticação ativa; conexão Docker em `db:5432`, conexão do host em
  `localhost` usando `FORWARD_DB_PORT` do Portal, mesmo banco/role/senha.
- API recriada com a configuração persistida; workers Laravel existentes
  reiniciados após consulta confirmar zero jobs reservados. Worker RAG não iniciado.
- `./start.sh` detecta a integração Docker habilitada e inicia somente a API RAG;
  `--no-rag` permite gerenciamento separado. Adapter/contrato HTTP preservados.
- 27 testes pertinentes do Portal passaram, 108 asserções, usando SQLite em
  memória e integrações simuladas. Teste existente de integração atualizado para
  o endpoint DNS Docker.
- Diagnóstico real Laravel: RAG ready, HTTP 200. Adapter real autenticado com UUID
  inválido recebeu rejeição controlada; POST autenticado inválido confirmou HTTP
  422, antes de recuperação/auditoria. Nenhuma consulta válida foi executada.
- A regra de sincronizar `.env` com futuras mudanças em `.env.example` foi
  registrada nos AGENTS.md do Portal e do RAG. Segredos permanecem fora do Git.

## Atualização documental de 11/11/2026

Os resultados de 86 testes acima pertencem à validação inicial da dockerização.
O registro posterior da implementação documental é de 97 testes RAG aprovados,
com dependências de banco simuladas/destino inválido; não é benchmark do corpus.
O Portal passou em 261 testes PHP (1420 asserções) na sessão, além de gerações
reais locais sobre snapshots existentes, sem ingestão ou reindexação.

A engenharia foi atualizada com autorização após a atividade inicial; a indicação
anterior de atualização pendente é histórica. Ver [estado compartilhado](../../intelireg-engineering/state/current-state.md).
O endpoint aditivo `/v1/rag/document` foi implementado depois do transporte
Docker; sua leitura foi validada com o cliente real do Portal. Qualidade de
retrieval, worker de indexação, backup/restore e servidor principal continuam
sem homologação nesta entrega. [Transição de instalação existente](../../intelireg/docs/RAG_DEPLOYMENT_TRANSITION.md).
