from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from intelireg import settings
from intelireg.db import get_conn
from intelireg.jobs import fetch_next_job, mark_done, mark_failed


def normalize_for_hash(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fake_embedding_1536(seed_hex: str) -> str:
    """
    Placeholder determinístico para o MVP: gera um vetor(1536) pseudo-aleatório.
    Substituir depois por chamada real ao provedor de embeddings.
    Retorna string no formato aceito pelo pgvector: [0.1,0.2,...]
    """
    seed = int(seed_hex[:16], 16)
    rng = random.Random(seed)
    vals = [rng.uniform(-1.0, 1.0) for _ in range(1536)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


def load_nodes(cur, version_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT node_id, path, heading_text, heading_level, text_normalized, node_index
        FROM nodes
        WHERE version_id = %s
        ORDER BY node_index ASC
        """,
        (version_id,),
    )
    rows = cur.fetchall()
    return [
        {
            "node_id": r[0],
            "path": r[1],
            "heading_text": r[2] or "",
            "heading_level": r[3],
            "text": r[4] or "",
            "node_index": r[5],
        }
        for r in rows
    ]


def build_chunks_from_nodes(
    nodes: List[Dict[str, Any]],
    pipeline_version: str,
    chunk_target_words: int,
    chunk_min_words: int,
    chunk_max_words: int,
    overlap_words: int,
) -> List[Dict[str, Any]]:
    """
    Chunking por "words" (proxy de tokens) para o MVP.
    Overlap respeita fronteiras de node: repete nodes inteiros do final do chunk anterior
    até atingir overlap_words.
    """

    def node_segment(n: Dict[str, Any]) -> str:
        h = (n.get("heading_text") or "").strip()
        t = (n.get("text") or "").strip()
        if h and t:
            return f"{h}\n{t}"
        return h or t

    chunks: List[Dict[str, Any]] = []
    current_nodes: List[Dict[str, Any]] = []
    current_text_parts: List[str] = []
    current_word_count = 0

    def finalize_chunk(
        chunk_index: int,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        nonlocal current_nodes, current_text_parts, current_word_count

        if not current_text_parts:
            return None, []

        chunk_text = "\n\n".join(current_text_parts).strip()
        tokens_count = len(chunk_text.split())  # proxy simples

        # node_refs com offsets aproximados (char offsets no texto final)
        node_refs = []
        cursor = 0
        for n in current_nodes:
            seg = node_segment(n).strip()
            if not seg:
                continue

            pos = chunk_text.find(seg, cursor)
            if pos < 0:
                pos = cursor

            start = pos
            end = pos + len(seg)
            cursor = end

            node_refs.append(
                {
                    "node_id": str(n["node_id"]),
                    "path": n.get("path"),
                    "heading": n.get("heading_text"),
                    "char_start": start,
                    "char_end": end,
                }
            )

        chunk_hash = sha256_hex(pipeline_version + "|" + normalize_for_hash(chunk_text))

        chunk = {
            "chunk_index": chunk_index,
            "chunk_hash": chunk_hash,
            "text": chunk_text,
            "node_refs": node_refs,
            "tokens_count": tokens_count,
        }

        # overlap: pega nodes do fim até atingir overlap_words
        overlap: List[Dict[str, Any]] = []
        ow = 0
        for n in reversed(current_nodes):
            seg = node_segment(n)
            w = len(seg.split())
            if w == 0:
                continue
            overlap.append(n)
            ow += w
            if ow >= overlap_words:
                break
        overlap = list(reversed(overlap))

        # reset
        current_nodes = []
        current_text_parts = []
        current_word_count = 0

        return chunk, overlap

    chunk_idx = 0
    overlap_buffer: List[Dict[str, Any]] = []

    for n in nodes:
        seg = node_segment(n).strip()
        if not seg:
            continue
        seg_words = len(seg.split())

        # se começando um novo chunk, aplica overlap
        if not current_text_parts and overlap_buffer:
            for on in overlap_buffer:
                oseg = node_segment(on).strip()
                if not oseg:
                    continue
                current_nodes.append(on)
                current_text_parts.append(oseg)
                current_word_count += len(oseg.split())
            overlap_buffer = []

        # se estoura max e já tem mínimo, fecha chunk
        if (current_word_count + seg_words) > chunk_max_words and current_word_count >= chunk_min_words:
            chunk, overlap = finalize_chunk(chunk_idx)
            if chunk:
                chunks.append(chunk)
                chunk_idx += 1
            overlap_buffer = overlap

        # adiciona node atual
        current_nodes.append(n)
        current_text_parts.append(seg)
        current_word_count += seg_words

        # se atingiu target e já tem mínimo, fecha chunk
        if current_word_count >= chunk_target_words and current_word_count >= chunk_min_words:
            chunk, overlap = finalize_chunk(chunk_idx)
            if chunk:
                chunks.append(chunk)
                chunk_idx += 1
            overlap_buffer = overlap

    # flush final
    if current_text_parts:
        chunk, _ = finalize_chunk(chunk_idx)
        if chunk:
            chunks.append(chunk)

    return chunks


def process_index_version(version_id: str, pipeline_version: str, embedding_model_id: str) -> int:
    """
    Gera embedding_chunks + chunk_embeddings (placeholder) e marca versão como INDEXED.
    Retorna número de chunks criados.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # valida versão e status
            cur.execute(
                "SELECT status FROM document_versions WHERE version_id = %s",
                (version_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"version_id não encontrado: {version_id}")

            status = row[0]
            if status != "READY_FOR_INDEX":
                raise RuntimeError(
                    f"version_id {version_id} status inválido: {status} (esperado READY_FOR_INDEX)"
                )

            nodes = load_nodes(cur, version_id)
            if not nodes:
                raise RuntimeError(f"version_id {version_id} não possui nodes")

            # idempotência/reindex: limpa chunks/embeddings dessa versão + pipeline_version
            cur.execute(
                """
                DELETE FROM chunk_embeddings
                WHERE pipeline_version = %s
                  AND chunk_id IN (
                    SELECT chunk_id
                    FROM embedding_chunks
                    WHERE version_id = %s AND pipeline_version = %s
                  )
                """,
                (pipeline_version, version_id, pipeline_version),
            )
            cur.execute(
                """
                DELETE FROM embedding_chunks
                WHERE version_id = %s AND pipeline_version = %s
                """,
                (version_id, pipeline_version),
            )

            chunks = build_chunks_from_nodes(
                nodes,
                pipeline_version=pipeline_version,
                chunk_target_words=settings.CHUNK_TARGET_WORDS,
                chunk_min_words=settings.CHUNK_MIN_WORDS,
                chunk_max_words=settings.CHUNK_MAX_WORDS,
                overlap_words=settings.CHUNK_OVERLAP_WORDS,
            )

            # insere chunks + embeddings
            for c in chunks:
                cur.execute(
                    """
                    INSERT INTO embedding_chunks
                      (version_id, pipeline_version, chunk_index, chunk_hash, text, node_refs, tokens_count)
                    VALUES
                      (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    RETURNING chunk_id
                    """,
                    (
                        version_id,
                        pipeline_version,
                        c["chunk_index"],
                        c["chunk_hash"],
                        c["text"],
                        json.dumps(c["node_refs"]),
                        c["tokens_count"],
                    ),
                )
                chunk_id = cur.fetchone()[0]

                # embedding placeholder determinístico (substituir depois por embeddings reais)
                vec = fake_embedding_1536(c["chunk_hash"])
                cur.execute(
                    """
                    INSERT INTO chunk_embeddings
                      (chunk_id, embedding_model_id, pipeline_version, embedding)
                    VALUES
                      (%s, %s, %s, %s::vector)
                    """,
                    (chunk_id, embedding_model_id, pipeline_version, vec),
                )

            # marca versão como indexada
            cur.execute(
                "UPDATE document_versions SET status = 'INDEXED' WHERE version_id = %s",
                (version_id,),
            )

        conn.commit()

    return len(chunks)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Index worker MVP: consome IndexVersionJob e gera chunks/FTS/embeddings"
    )
    ap.add_argument("--once", action="store_true", help="Processa no máximo 1 job e sai")
    ap.add_argument(
        "--sleep",
        type=float,
        default=settings.INDEX_WORKER_SLEEP_SECONDS,
        help="Sleep quando não houver jobs (segundos)",
    )
    args = ap.parse_args()

    worker_id = os.getenv("WORKER_ID", settings.INDEX_WORKER_ID_DEFAULT)

    while True:
        job = fetch_next_job(worker_id=worker_id)
        if not job:
            if args.once:
                return
            time.sleep(args.sleep)
            continue

        try:
            if job.type != "IndexVersionJob":
                raise RuntimeError(f"tipo de job não suportado no MVP: {job.type}")

            version_id = job.payload["version_id"]

            # Fonte de verdade: payload do job (fallback no settings)
            pipeline_version = job.payload.get("pipeline_version", settings.PIPELINE_VERSION)
            embedding_model_id = job.payload.get("embedding_model_id", settings.EMBEDDING_MODEL_ID)

            n = process_index_version(version_id, pipeline_version, embedding_model_id)
            mark_done(job.job_id)
            print(
                f"[index_worker] done job_id={job.job_id} version_id={version_id} pipeline={pipeline_version} chunks={n}"
            )

        except Exception as e:
            mark_failed(job.job_id, str(e), backoff_seconds=15)
            print(f"[index_worker] failed job_id={job.job_id}: {e}")

        if args.once:
            return


if __name__ == "__main__":
    main()
