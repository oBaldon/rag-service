# Retirada de mecanismos legados após dockerização

Data: 07/11/2026. Status: alterações locais aplicadas; sem operações de banco.
Base Git RAG: `7751e0006df3cca64de143821237108ec15651d1`, com alterações da
Dockerização já presentes no working tree e preservadas nesta revisão.

## Removido ou substituído

- `scripts/run_api.sh`: inicializador paralelo no host, carregava `.env` por shell
  e iniciava Uvicorn fora do Compose. Substituído por `docker compose up -d --wait
  rag-api`, ou pelo `start.sh` do Portal. Não havia chamadas internas ao script.
- `scripts/run_intelireg_mvp.txt`: roteiro experimental começando com reset,
  instalação/restart PostgreSQL no host e terminando com ingestão/indexação e
  atualização manual de jobs. Não serve para a infraestrutura compartilhada.
  As únicas referências internas eram comentários de origem nos scripts golden,
  agora ajustados. O histórico Git mantém a versão anterior.
- `scripts/smoke_api.sh`: substituído o smoke via localhost/curl e consultas que
  persistiam auditoria por readiness do container `rag-api`, sem publicar portas,
  carregar `.env` como shell ou executar recuperação. Agora falha com o status do
  healthcheck e funciona independentemente do diretório de chamada.
- Guia de API e requisitos: endereço interno Docker como referência; requisitos
  Python/psql no host identificados como desenvolvimento/administração opcionais.

## Preservado deliberadamente

Migrations SQL, `bootstrap_db.sh` e `reset_db.sh` são ferramentas administrativas,
não caminhos de boot. Golden, CLI, contratos v1, `/health` (alias de liveness) e
`/v1/rag/ask` permanecem: dockerização não torna essas funcionalidades obsoletas.
Os scripts golden exigem ferramentas fora da imagem e continuam fora do startup;
seu uso pode alterar dados. Não foram executados nem convertidos em entrypoints.
Não foram removidos venv, cache/modelos, corpus, volumes ou dados existentes.

## Verificação e reversão

`bash -n` passou nos três scripts alterados e `git diff --check` passou.
`scripts/smoke_api.sh` passou contra a API local previamente autorizada, tanto
com chamada a partir do Portal quanto com diretório de trabalho `/tmp`.
Nenhuma mudança na imagem, dependências, modelo,
pipeline ou contrato HTTP. Não é necessário rebuild/restart por esta limpeza.
Para reverter, restaure apenas os arquivos desta limpeza usando sua revisão-base
ou o diff revisado, preservando as demais alterações locais de dockerização.

Após autorização explícita do usuário, a engenharia foi atualizada: índice,
topologia Docker/banco, implantação, configuração de ambientes/IA, ADR-002,
sprint 001, estado atual, triagem e decisões pendentes. O planejamento de setembro
foi identificado como histórico. Produção, worker e qualidade de recuperação
continuam pendentes; nenhuma aprovação de produção foi inferida.
