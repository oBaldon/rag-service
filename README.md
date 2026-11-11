# InteliReg RAG Service

API FastAPI de recuperação regulatória e worker PostgreSQL de indexação. O Portal
consome `POST /v1/rag/query`; geração por LLM pertence ao Portal/Ollama.

## Docker e integração

A imagem Python 3.12 executa com UID/GID 10001, logs em stdout/stderr, PyTorch CPU,
modelo `intfloat/multilingual-e5-small@384` e pipeline existente. API e worker são
processos separados. Não há PostgreSQL novo nem volume de corpus: todos os dados
continuam no banco compartilhado. Só o cache Hugging Face tem volume próprio.

```text
Portal app/queue → rag-api:8088/v1/rag/query
                          ↓
rag-worker ───────────→ db:5432 / banco existente / schema intelireg
Bulário ─────────────→ db:5432 / bulario.* e public.bulas
```

O Compose próprio segue a autonomia já adotada pelo Bulário e conecta à rede
externa do Portal. O nome padrão é `intelireg-local_default`; ajuste
`INTELIREG_DOCKER_NETWORK` se a instalação usar outro nome. Não renomeie a rede
ou o volume PostgreSQL para instalar o RAG. O RAG não monta o acervo de PDFs do
Bulário e seu runtime atual não chama o produtor por HTTP.

### Configuração

1. Em instalação nova, copie `.env.example` para `.env`; preserve o arquivo de
   instalações existentes. Não incorpore `.env` na imagem.
2. Configure `RAG_DOCKER_DATABASE_URL` usando **o mesmo banco, usuário e senha da
   conexão atual**, trocando host/porta por `db:5432`. Exemplo sanitizado:
   `postgresql://intelireg:CHANGE_ME@db:5432/intelireg`. Codifique caracteres
   reservados da senha na URL. O `DATABASE_URL` do host permanece independente.
3. Preserve `PG_SCHEMA=intelireg`, pipeline, modelo, dimensão e opções semânticas
   compatíveis com o índice. `get_conn()` define o schema por conexão e confirma
   o SET antes das transações da aplicação. Isso evita confundir a fila RAG com
   `public.jobs` do Laravel.
4. Configure `APP_ENV` e `RAG_AUTH_REQUIRED` conforme o ambiente. Em produção,
   use `APP_ENV=production`, `RAG_AUTH_REQUIRED=true` e chave forte compartilhada
   com o Portal. Uma chave não vazia é exigida pela API mesmo quando
   `RAG_AUTH_REQUIRED=false`. Remova placeholders; `HF_TOKEN` é opcional para
   o modelo público. O runtime não recebe conexão privilegiada utilizável:
   `PG_SUPERUSER_URL` é sobrescrita para vazio.
5. No `.env` do Portal:

   ```dotenv
   RAG_ENABLED=true
   RAG_SERVICE_URL=http://rag-api:8088/v1/rag/query
   RAG_HEALTH_URL=http://rag-api:8088/health/ready
   RAG_DOCUMENT_URL=http://rag-api:8088/v1/rag/document
   # Configure RAG_API_KEY com a mesma chave do RAG, sem publicá-la.
   ```

O Compose injeta as opções do `.env` do RAG, sobrescrevendo apenas conexão Docker,
cache e CPU. Ele não publica porta no host. Para depuração, use override local
com `ports: ["127.0.0.1:8088:8088"]`. Não altere o contrato v1.

### Build e inicialização

Com PostgreSQL e rede do Portal já disponíveis, no diretório RAG:

```bash
docker compose config --quiet
docker compose build rag-api
docker compose up -d --wait --wait-timeout 180 rag-api
docker compose exec -T rag-api python docker/healthcheck.py
docker compose logs -f rag-api
```

Para iniciar a stack pelo Portal, depois de configurar ambos os ambientes:

```bash
./start.sh --rag
# Imagens existentes, sem novo build:
./start.sh --rag --no-build
```

O `start.sh` mantém seu fluxo existente de dependências/assets e PostgreSQL;
acrescenta a espera do RAG antes de iniciar a aplicação. `RAG_PROJECT_DIR` permite
mudar o caminho do repositório irmão. Não modifica `.env` nem habilita RAG no
Laravel automaticamente. Configuração Laravel em cache deve ser atualizada pelo
procedimento de implantação do Portal.

Quando o Portal tem `RAG_ENABLED=true` e a URL `http://rag-api:8088/v1/rag/query`,
`./start.sh` inicia a API automaticamente. `--no-rag` desativa esse gerenciamento
na execução atual. Alterações em `.env.example` devem ser acompanhadas pela
atualização do `.env` local, preservando os segredos e particularidades do ambiente.

O worker é **opcional e explícito**, pois pode alterar jobs/embeddings existentes:

```bash
# Somente após autorizar o processamento da fila no ambiente identificado:
docker compose --profile indexing up -d --wait rag-api rag-worker
# Ou, a partir do Portal:
./start.sh --rag-worker
```

API e worker aguardam prontidão de configuração, banco, schema e perfil semântico
antes do comando principal, com prazo `RAG_STARTUP_TIMEOUT` (120s por padrão).
O Compose usa `/health/ready` na API; o worker depende da API saudável e realiza
seu próprio preflight. Seu estado `running` indica processo ativo, não progresso
de indexação; monitore logs e jobs para progresso. Reinícios usam
`unless-stopped`. Nenhum startup aplica migrations, bootstrap, reset ou reindexação.

Readiness não carrega o modelo nem comprova inferência. O primeiro embedding
baixa o modelo da Hugging Face se ausente; requer saída HTTPS e espaço no cache.
Para operação offline, provisione previamente o mesmo modelo no cache com UID
10001 e teste inferência em ambiente de homologação. Cache montado por bind precisa
ser gravável por esse UID; o volume nomeado novo herda a propriedade da imagem.
Não há necessidade de Ollama ou Redis para este RAG.

### Atualização, produção e reversão

```bash
# Após atualizar código, preservando a configuração:
docker compose build rag-api
docker compose up -d --no-deps --wait rag-api
# Se o worker já estiver autorizado/ativo, atualize-o separadamente:
docker compose --profile indexing up -d --no-deps rag-worker
```

Publique a imagem em registry e configure `RAG_IMAGE` com tag imutável/digest para
produção; use a mesma imagem para API e worker. Não use o `start.sh` local como
substituto do procedimento de deploy de produção. Preserve modelo/cache e
versione alterações das bibliotecas com regressão antes de promover a imagem.
As dependências transitivas estão travadas em `requirements-docker.lock`, com
versões diretas alinhadas ao venv local observado. Para regenerar deliberadamente:

```bash
uv pip compile requirements.txt -c docker/constraints.in --python-version 3.12 \
  --torch-backend cpu --output-file requirements-docker.lock --no-header --emit-index-url
```

A versão CPU do torch também está explícita no Dockerfile; atualize os dois ao
mudar a constraint. A imagem base está fixada por digest; atualize-a de forma
deliberada com rebuild e regressão.

Para reverter, pare somente os containers RAG, restaure a imagem/configuração
anterior e recrie API/worker autorizado. Para retirar o RAG, desabilite sua
integração no Portal e remova as opções `--rag*` da inicialização:

```bash
docker compose --profile indexing stop rag-worker rag-api
```

Não remova volumes, não derrube a stack PostgreSQL compartilhada, não execute
`reset_db.sh` ou `golden/01_update_kb.sh`. Migrations SQL são etapa administrativa
separada, com revisão/backup e autorização específica; não são condição a ser
corrigida automaticamente pelo container.

## Validação segura

Consulte [docs/docker-validation.md](docs/docker-validation.md) para evidências,
revisões-base e limites da validação. Testes unitários simulam acesso a banco e
embeddings. Consultas HTTP RAG válidas persistem auditoria em `rag_runs`; por isso
não são testes somente leitura. O worker também altera dados. Valide recuperação,
embeddings e consumo de jobs em homologação, com corpus isolado e autorização.

Contratos: [docs/api_contract_v1.md](docs/api_contract_v1.md).
Requisitos e operação no host: [system_requirements.md](system_requirements.md).

## Mecanismos legados retirados

O startup no host e o roteiro MVP de reset foram retirados. O smoke operacional
agora usa o container existente, sem consultas que persistam auditoria:

```bash
./scripts/smoke_api.sh
```

Veja [registro da limpeza](docs/legacy-cleanup.md) para arquivos removidos,
ferramentas administrativas preservadas, compatibilidade e reversão.

## Texto documental para resumo no Portal

`POST /v1/rag/document` recupera todos os nós armazenados de uma versão normativa,
com hash e proveniência, sem seleção top-k. O Portal gera o resumo; ambiguidades
não são resolvidas por escolha arbitrária. Veja [contrato](docs/full-document-contract.md).

### Instalação existente e preservação do acervo

`git pull` não migra banco, volumes nem `.env`. Reutilizar a instância/volume
existente com índice compatível evita reingestão. Se o PostgreSQL estava fora do
Docker, planejar conexão específica ou backup/restauração para o destino antes
de ativar workers. Não apontar silenciosamente para um volume novo vazio.
Consulte [guia de transição no Portal](../intelireg/docs/RAG_DEPLOYMENT_TRANSITION.md).

O Portal resume por IDs de artigos/seções, mas esse processamento não altera os
nós fornecidos pelo RAG. `all_stored_nodes` não comprova fidelidade ao original;
limitações de extração precisam de investigação própria, sem reingestão como
efeito colateral do deploy. Ver [operação e limitações no Portal](../intelireg/docs/NORM_SUMMARY.md).
