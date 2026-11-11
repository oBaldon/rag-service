"""Espera somente leitura; nunca aplica migrations ou consome jobs no preflight."""
import os
import sys
import time

from intelireg.readiness import check_readiness


def main():
    deadline = time.monotonic() + float(os.getenv("RAG_STARTUP_TIMEOUT", "120"))
    while True:
        ready, _ = check_readiness()
        if ready:
            break
        if time.monotonic() >= deadline:
            sys.exit("RAG não está pronto: confira banco, schema e /health/ready.")
        print("Aguardando prontidão do banco/schema RAG...", flush=True)
        time.sleep(3)
    if len(sys.argv) < 2:
        sys.exit("Comando da aplicação ausente.")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
