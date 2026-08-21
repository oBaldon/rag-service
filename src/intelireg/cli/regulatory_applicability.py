from __future__ import annotations

import argparse
import json

from intelireg.regulatory_applicability import (
    import_regulatory_applicability,
    load_import_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Valida/importa assertions curadas de situação e relações normativas. "
            "Dry-run por padrão."
        )
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Arquivo JSON conforme config/regulatory_applicability.schema.json.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Persiste o lote após validação. Sem esta flag, apenas dry-run.",
    )
    args = parser.parse_args()

    plan = load_import_file(args.file)
    result = import_regulatory_applicability(plan, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
