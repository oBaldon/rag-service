#!/usr/bin/env bash
set -euo pipefail
set -a
source .env
set +a

BASE_URL="http://127.0.0.1:8088"
API_KEY="${RAG_API_KEY:-'uma_chave_interna'}"

# Opcional: coloque um version_id real para testes de escopo
VERSION_ID=""   # ex: "121bd227-6089-464b-b79d-6898245d9b60"

Q='Quais são os requisitos para alteração pós-registro?'

outdir="storage/api_test_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$outdir"

echo "== 0) Health =="
curl -si "$BASE_URL/health" | tee "$outdir/00_health.headers.txt" >/dev/null
echo "OK: health"

echo
echo "== 1) Auth (deve dar 401 com key errada) =="
set +e
curl -si -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: WRONG_KEY" \
  -d "{\"question\":\"$Q\"}" \
  | tee "$outdir/01_auth_wrong.headers.txt" >/dev/null
set -e
if ! grep -q "401" "$outdir/01_auth_wrong.headers.txt"; then
  echo "FAIL: expected 401 on wrong api key"
  exit 1
fi
echo "OK: auth wrong key returns 401"

echo
echo "== 2) Query padrão (híbrido) =="
curl -si -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{
    \"question\": \"$Q\",
    \"top_k\": 5,
    \"n1_fts\": 30,
    \"n2_vec\": 30,
    \"rrf_k\": 60
  }" \
  | tee "$outdir/02_query_hybrid.headers_and_body.txt" >/dev/null
echo "OK: hybrid"

echo
echo "== 3) FTS-only (n2_vec=0) =="
curl -s -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{
    \"question\": \"$Q\",
    \"top_k\": 5,
    \"n1_fts\": 80,
    \"n2_vec\": 0,
    \"rrf_k\": 60
  }" \
  | tee "$outdir/03_query_fts_only.json" >/dev/null
echo "OK: fts-only"

echo
echo "== 4) Vec-only (n1_fts=0) =="
curl -s -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{
    \"question\": \"$Q\",
    \"top_k\": 5,
    \"n1_fts\": 0,
    \"n2_vec\": 80,
    \"rrf_k\": 60
  }" \
  | tee "$outdir/04_query_vec_only.json" >/dev/null
echo "OK: vec-only"

echo
echo "== 5) top_k variando (1, 10) =="
curl -s -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"$Q\",\"top_k\":1,\"n1_fts\":30,\"n2_vec\":30,\"rrf_k\":60}" \
  | tee "$outdir/05_query_top1.json" >/dev/null

curl -s -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"$Q\",\"top_k\":10,\"n1_fts\":30,\"n2_vec\":30,\"rrf_k\":60}" \
  | tee "$outdir/06_query_top10.json" >/dev/null
echo "OK: top_k variations"

echo
echo "== 6) rrf_k variando (10 vs 120) =="
curl -s -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"$Q\",\"top_k\":5,\"n1_fts\":30,\"n2_vec\":30,\"rrf_k\":10}" \
  | tee "$outdir/07_query_rrf10.json" >/dev/null

curl -s -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"$Q\",\"top_k\":5,\"n1_fts\":30,\"n2_vec\":30,\"rrf_k\":120}" \
  | tee "$outdir/08_query_rrf120.json" >/dev/null
echo "OK: rrf_k variations"

echo
echo "== 7) version_id (se informado) =="
if [[ -n "$VERSION_ID" ]]; then
  curl -s -X POST "$BASE_URL/v1/rag/query" \
    -H "Content-Type: application/json" \
    -H "x-api-key: $API_KEY" \
    -d "{
      \"question\": \"$Q\",
      \"version_id\": \"$VERSION_ID\",
      \"top_k\": 5,
      \"n1_fts\": 30,
      \"n2_vec\": 30,
      \"rrf_k\": 60
    }" \
    | tee "$outdir/09_query_version_id.json" >/dev/null
  echo "OK: version_id filtered"
else
  echo "SKIP: VERSION_ID vazio"
fi

echo
echo "== 8) Validação: payload inválido (question vazia) deve dar 422 =="
set +e
curl -si -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"\"}" \
  | tee "$outdir/10_invalid_question.headers.txt" >/dev/null
set -e
if ! grep -q "422" "$outdir/10_invalid_question.headers.txt"; then
  echo "FAIL: expected 422 on invalid question"
  exit 1
fi
echo "OK: invalid payload returns 422"

echo
echo "== 9) Caso degenerado: n1_fts=0 e n2_vec=0 (deve retornar 400) =="

resp_file="$outdir/11_both_off.headers_and_body.txt"
curl -si -X POST "$BASE_URL/v1/rag/query" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"$Q\",\"top_k\":5,\"n1_fts\":0,\"n2_vec\":0}" \
  | tee "$resp_file" >/dev/null

if ! head -n 1 "$resp_file" | grep -q "400"; then
  echo "FAIL: expected 400 in both-off. First line:"
  head -n 1 "$resp_file"
  exit 1
fi
echo "OK: both-off returns 400"

echo
echo "== 10) /v1/rag/ask (deve retornar 200) =="
set +e
curl -si -X POST "$BASE_URL/v1/rag/ask" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{\"question\":\"$Q\",\"top_k\":5,\"n1_fts\":30,\"n2_vec\":0,\"rrf_k\":60}" \
  | tee "$outdir/12_ask.headers_and_body.txt" >/dev/null
set -e
if ! head -n 1 "$outdir/12_ask.headers_and_body.txt" | grep -q "200"; then
  echo "FAIL: expected 200 on /v1/rag/ask"
  head -n 1 "$outdir/12_ask.headers_and_body.txt"
  exit 1
fi
echo "OK: /ask returns 200"

echo "DONE. Outputs em: $outdir"