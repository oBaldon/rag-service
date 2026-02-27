#!/usr/bin/env bash
set -euo pipefail

RUNS_DIR="../storage/runs"
OUT_FILE="${RUNS_DIR}/manual_prompt.txt"

LATEST="$(find "$RUNS_DIR" -maxdepth 1 -type f -name '*_query.json' -printf '%f\n' \
  | sort -V | tail -n 1 | sed "s|^|$RUNS_DIR/|")"

if [ -z "${LATEST:-}" ] || [ ! -f "$LATEST" ]; then
  echo "ERRO: não encontrei arquivos em $RUNS_DIR/*_query.json"
  exit 1
fi

jq -r '
"Você é um assistente de inteligência regulatória especializado em normas da Anvisa.\n\n" +
"INSTRUÇÕES:\n" +
"- Responda exclusivamente com base nos trechos fornecidos abaixo.\n" +
"- Não utilize conhecimento externo.\n" +
"- Organize a resposta nos seguintes blocos:\n" +
"  1) Composição e elegibilidade (≤ 0,2% THC)\n" +
"  2) Condições de prescrição e uso\n" +
"  3) Restrições regulatórias do produto\n" +
"  4) Rotulagem, embalagem e rastreabilidade\n" +
"  5) Requisitos da empresa e Autorização Sanitária\n" +
"- Cite sempre o artigo correspondente no formato: (RDC 327/2019, Art. X).\n" +
"- Se alguma informação não constar nos trechos, declare explicitamente: \"Não consta nos trechos fornecidos.\"\n\n" +
"PERGUNTA:\n" + .query + "\n\n" +
"CONTEXTO REGULATÓRIO:\n\n" +
(
  .results
  | sort_by(.rank)
  | map(
      "### TRECHO " + (.rank|tostring) + "\n" +
      "Documento: " + (.document.title // "sem_titulo") + "\n" +
      "Artigos/Nós: " + ((.citations | map(.heading) | unique | join(", ")) // "sem_heading") + "\n\n" +
      .chunk.text + "\n"
    )
  | join("\n----------------------------------------\n\n")
)
' "$LATEST" > "$OUT_FILE"

echo "OK: gerado $OUT_FILE a partir de $LATEST"
