# Contrato de recuperação documental — v1

Data: 11/11/2026. Implementado localmente. Independente de `/v1/rag/query`.

`POST /v1/rag/document`, autenticado pela mesma política e chave da API.
Corpo: `question` obrigatória (até 10000 caracteres), `version_id` UUID opcional.
Exemplos: `Resuma RDC 327/2019`, `Resuma a norma 1.015/2015`.
O servidor recupera texto; não gera o resumo. O Portal coordena LLM e fila.

Identificadores tipados reutilizam o parser regulatório. Referências genéricas
`norma número/ano` são aceitas sem inferir família. Havendo várias candidatas,
retorna `409 norm_ambiguous`, lista de títulos, órgãos e version_id, e mensagem
pedindo esclarecimento. O órgão explícito na pergunta pode desambiguar. A API não
busca normas novas na Internet. A versão padrão é a mais recente no acervo por
captura/criação, o que não comprova consolidação ou vigência.

Sucesso (HTTP 200): `schema_version=1`, `request_id`, `run_id` de correlação da
operação, `generated_at`, `document_id`, `version_id`, `title`, `source_org`,
`doc_type`, `source_url`, `final_url`, `captured_at`, `source_content_hash`,
`text`, `text_sha256`, `node_count`, `coverage=all_stored_nodes`, `limitations`.
Esse run_id não corresponde a uma consulta híbrida persistida em rag_runs.

`text` reúne heading_text e text_normalized de TODOS os nós da versão, em ordem
node_index. Não usa top_k ou embeddings. O hash SHA-256 é calculado sobre o UTF-8
exato desse texto. A operação é somente leitura no banco.

Erros controlados: 422 `norm_identifier_required`, 404 `norm_not_found`, 409
`norm_ambiguous`, 422 `norm_text_unavailable` (sem corpo documental ou com nós sem título e sem corpo), 422
`norm_text_incomplete` (sequência duplicada/com lacunas), 413 `norm_text_too_large`
(acima de 300000 caracteres). Requisições e autenticação seguem erros v1 existentes.
Há limite de inventário de 500 candidatas; excesso pede especificação adicional.

A cobertura certifica leitura dos nós armazenados, não integridade da extração
perante o original ou completude de anexos. Isso é declarado no contrato e deve
ser mantido no resumo do Portal. Modelo/pipeline/ingestão não foram alterados.

Testes: `tests/test_full_document.py`. Validação operacional: leitura via cliente
real do Portal, sem ingestão, reindexação, consulta híbrida ou escrita de auditoria.

Títulos estruturais sem corpo próprio (capítulos, seções e cabeçalhos) são
preservados; não significam por si só falha de extração. O parser aceita o órgão
Anvisa entre a família normativa e o número. Ano solicitado continua obrigatório
na correspondência quando informado; não há troca automática pelo ano existente.

## Limites do consumidor e da extração

O Portal atualmente monta síntese extrativa por IDs de artigos/seções de anexos.
Esse processamento é local ao snapshot e não reescreve os nós do RAG. Seu limite
de contexto por chamada é independente do limite HTTP de 300000 caracteres.
Blocos extensos podem exigir divisão no consumidor, sem nova ingestão.

Foi observado no snapshot da RDC 1.015/2026 um trecho associado ao Art. 4º com
reticências e `"(NR)"`. Sua associação/origem deve ser investigada; a recuperação
integral e o hash não detectam esse tipo de problema jurídico/estrutural. Não
houve correção do acervo ou reingestão nessa entrega. Ver [estado da síntese no
Portal](../../intelireg/docs/NORM_SUMMARY.md).
