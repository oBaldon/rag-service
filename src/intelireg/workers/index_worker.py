from __future__ import annotations

import argparse
import traceback
import hashlib
import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from intelireg import settings
from intelireg.db import get_conn
from intelireg.jobs import fetch_next_job, mark_done, mark_failed


# -------------------- hashing/normalização --------------------

def normalize_for_hash(s: str) -> str:
    # colapsa whitespace + casefold => determinístico e estável
    return " ".join((s or "").split()).casefold()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def chunk_hash(pipeline_version: str, chunk_text: str) -> str:
    # IMPORTANTE: não incluir chunk_index (senão texto igual vira hash diferente)
    return sha256_hex(pipeline_version + "|" + normalize_for_hash(chunk_text))


# -------------------- embedding placeholder --------------------

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


# -------------------- DB --------------------

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


# -------------------- chunking --------------------

_sentence_split_re = re.compile(r"(?<=[\.\!\?])\s+")


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

    Correções importantes vs versão anterior:
    - NÃO cria chunk "só de overlap" (duplica chunk anterior).
    - Só aplica overlap quando existe um node real (não vazio) a ser adicionado.
    - Overlap não inclui unidades gigantes (evita repetir mega-node).
    - chunk_hash independe de chunk_index (texto igual => hash igual).
    - Dedup em memória para evitar chunks repetidos.
    - Split "soft" de node muito grande, baseado no target, para evitar chunk gigantesco.
    """

    # Uma "unidade" é o menor bloco adicionável ao chunk.
    # Pode ser um node inteiro ou um pedaço (split) de um node muito grande.
    Unit = Dict[str, Any]

    def node_segment(n: Dict[str, Any]) -> str:
        h = (n.get("heading_text") or "").strip()
        t = (n.get("text") or "").strip()
        if h and t:
            return f"{h}\n{t}"
        return h or t

    def _split_by_paragraphs(text: str) -> List[str]:
        # tenta preservar blocos em normas: quebra por linhas em branco ou \n
        if "\n\n" in text:
            parts = [p.strip() for p in text.split("\n\n")]
        elif "\n" in text:
            parts = [p.strip() for p in text.split("\n")]
        else:
            parts = [text.strip()]
        return [p for p in parts if p]

    def _split_by_sentences(text: str) -> List[str]:
        parts = [p.strip() for p in _sentence_split_re.split(text.strip())]
        return [p for p in parts if p]

    def split_segment_soft(seg: str) -> List[str]:
        """
        Divide um segmento grande em pedaços ~target para evitar chunk gigante.
        Mesmo que CHUNK_MAX_WORDS seja alto, aqui usamos um "soft max":
          soft_max = min(chunk_max_words, chunk_target_words * 2)
        e part_max = min(chunk_max_words, chunk_target_words)
        """
        seg = (seg or "").strip()
        if not seg:
            return []

        words = seg.split()
        if len(words) <= max(1, chunk_max_words):
            # ainda pode ser enorme se chunk_max_words for muito alto;
            # então aplicamos "soft cap" relativo ao target:
            soft_max = min(chunk_max_words, max(chunk_target_words * 2, chunk_min_words))
            if len(words) <= soft_max:
                return [seg]

        # tentar separar heading do corpo para repetir o heading em cada pedaço
        if "\n" in seg:
            head, body = seg.split("\n", 1)
            head = head.strip()
            body = body.strip()
        else:
            head, body = "", seg

        # quanto cabe por pedaço (inclui heading repetido)
        head_words = len(head.split()) if head else 0
        part_max = min(chunk_max_words, max(chunk_target_words, chunk_min_words, 1))
        budget = max(50, part_max - head_words)  # garante algum corpo por pedaço

        # preferir parágrafos, depois sentenças, depois palavras
        chunks_body: List[str] = []
        candidates = _split_by_paragraphs(body)
        if len(candidates) == 1 and len(candidates[0].split()) > budget:
            candidates = _split_by_sentences(body)
        if len(candidates) == 1 and len(candidates[0].split()) > budget:
            # fallback: palavra a palavra
            candidates = body.split()

            buf_words: List[str] = []
            for w in candidates:
                buf_words.append(w)
                if len(buf_words) >= budget:
                    chunks_body.append(" ".join(buf_words).strip())
                    buf_words = []
            if buf_words:
                chunks_body.append(" ".join(buf_words).strip())
        else:
            buf: List[str] = []
            buf_wc = 0
            for c in candidates:
                wc = len(c.split())
                if buf and (buf_wc + wc) > budget:
                    chunks_body.append("\n".join(buf).strip())
                    buf = []
                    buf_wc = 0
                buf.append(c)
                buf_wc += wc
            if buf:
                chunks_body.append("\n".join(buf).strip())

        out: List[str] = []
        for cb in chunks_body:
            if head and cb:
                out.append(f"{head}\n{cb}".strip())
            else:
                out.append((cb or head).strip())
        return [x for x in out if x]

    def node_to_units(n: Dict[str, Any]) -> List[Unit]:
        seg = node_segment(n).strip()
        if not seg:
            return []
        parts = split_segment_soft(seg)
        units: List[Unit] = []
        for idx, p in enumerate(parts):
            units.append(
                {
                    "node_id": str(n["node_id"]),
                    "path": n.get("path"),
                    "heading": n.get("heading_text"),
                    "text": p.strip(),
                    "part_index": idx,
                }
            )
        return units

    def unit_words(u: Unit) -> int:
        return len((u.get("text") or "").split())

    # estado do chunk atual
    chunks: List[Dict[str, Any]] = []
    current_units: List[Unit] = []
    current_word_count = 0
    current_has_new_content = False  # <- crucial: evita chunk "só de overlap"

    overlap_buffer: List[Unit] = []
    seen_chunk_hashes: set[str] = set()
    last_norm: Optional[str] = None

    def apply_overlap_if_needed() -> None:
        nonlocal current_units, current_word_count, overlap_buffer
        if current_units:
            return
        if not overlap_buffer:
            return
        # aplica overlap sem marcar como "conteúdo novo"
        for ou in overlap_buffer:
            txt = (ou.get("text") or "").strip()
            if not txt:
                continue
            current_units.append(ou)
            current_word_count += len(txt.split())
        overlap_buffer = []

    def compute_overlap_from_current() -> List[Unit]:
        if overlap_words <= 0:
            return []
        overlap: List[Unit] = []
        ow = 0
        for u in reversed(current_units):
            w = unit_words(u)
            if w <= 0:
                continue
            # se uma unidade sozinha já excede overlap_words, NÃO repetimos ela
            # (evita repetir mega-node/mega-chunk)
            if w > overlap_words:
                return []
            overlap.append(u)
            ow += w
            if ow >= overlap_words:
                break
        overlap.reverse()
        return overlap

    def finalize_chunk(chunk_index: int) -> Tuple[Optional[Dict[str, Any]], List[Unit]]:
        nonlocal current_units, current_word_count, current_has_new_content, last_norm

        if not current_units:
            return None, []

        # se não adicionou nada novo (apenas overlap), não emite chunk
        if not current_has_new_content:
            # limpa estado e não propaga overlap (senão pode ficar loopando)
            current_units = []
            current_word_count = 0
            current_has_new_content = False
            return None, []

        parts = [(u.get("text") or "").strip() for u in current_units if (u.get("text") or "").strip()]
        if not parts:
            current_units = []
            current_word_count = 0
            current_has_new_content = False
            return None, []

        chunk_text = "\n\n".join(parts).strip()
        norm = normalize_for_hash(chunk_text)
        if last_norm == norm:
            # evita duplicar por algum bug residual
            current_units = []
            current_word_count = 0
            current_has_new_content = False
            return None, []

        h = chunk_hash(pipeline_version, chunk_text)
        if h in seen_chunk_hashes:
            current_units = []
            current_word_count = 0
            current_has_new_content = False
            return None, []

        seen_chunk_hashes.add(h)
        last_norm = norm

        tokens_count = len(chunk_text.split())  # proxy simples

        # node_refs com offsets determinísticos (sem find)
        node_refs = []
        cursor = 0
        for i, u in enumerate(current_units):
            seg = (u.get("text") or "").strip()
            if not seg:
                continue
            if i > 0:
                cursor += 2  # "\n\n"
            start = cursor
            end = start + len(seg)
            cursor = end
            node_refs.append(
                {
                    "node_id": u.get("node_id"),
                    "path": u.get("path"),
                    "heading": u.get("heading"),
                    "char_start": start,
                    "char_end": end,
                }
            )

        overlap = compute_overlap_from_current()

        chunk = {
            "chunk_index": chunk_index,
            "chunk_hash": h,
            "text": chunk_text,
            "node_refs": node_refs,
            "tokens_count": tokens_count,
        }

        # reset estado
        current_units = []
        current_word_count = 0
        current_has_new_content = False

        return chunk, overlap

    # transforma nodes -> units e processa
    chunk_idx = 0

    for n in nodes:
        units = node_to_units(n)
        if not units:
            # node vazio: NÃO aplica overlap aqui (evita chunk só de overlap)
            continue

        for u in units:
            u_text = (u.get("text") or "").strip()
            if not u_text:
                continue

            u_words = len(u_text.split())

            # Se estamos para começar chunk novo e temos overlap pendente, aplica agora
            apply_overlap_if_needed()

            # Se adicionar estoura max e já temos mínimo, fecha chunk antes de adicionar u
            if (
                current_units
                and (current_word_count + u_words) > chunk_max_words
                and current_word_count >= chunk_min_words
            ):
                chunk, overlap = finalize_chunk(chunk_idx)
                if chunk:
                    chunks.append(chunk)
                    chunk_idx += 1
                overlap_buffer = overlap

                # novo chunk: aplica overlap imediatamente (para o mesmo u)
                apply_overlap_if_needed()

            # adiciona unidade (conteúdo novo)
            current_units.append(u)
            current_word_count += u_words
            current_has_new_content = True

            # Se atingiu target e já tem mínimo, fecha chunk
            if current_word_count >= chunk_target_words and current_word_count >= chunk_min_words:
                chunk, overlap = finalize_chunk(chunk_idx)
                if chunk:
                    chunks.append(chunk)
                    chunk_idx += 1
                overlap_buffer = overlap

    # flush final (não cria chunk se for só overlap)
    if current_units:
        chunk, _ = finalize_chunk(chunk_idx)
        if chunk:
            chunks.append(chunk)

    return chunks


# -------------------- processamento do job --------------------

def process_index_version(version_id: str, pipeline_version: str, embedding_model_id: str, force: bool = False) -> int:
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
            if status != "READY_FOR_INDEX" and not (force and status == "INDEXED"):
                raise RuntimeError(
                    f"version_id {version_id} status inválido: {status} "
                    f"(esperado READY_FOR_INDEX; ou INDEXED com force=true)"
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
            created = 0
            for c in chunks:
                cur.execute(
                    """
                    INSERT INTO embedding_chunks
                      (version_id, pipeline_version, chunk_index, chunk_hash, text, node_refs, tokens_count)
                    VALUES
                      (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (version_id, pipeline_version, chunk_hash)
                    DO NOTHING
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
                row = cur.fetchone()
                if not row:
                    # chunk duplicado (não deve acontecer, mas fica seguro)
                    continue
                chunk_id = row[0]
                created += 1

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

    return created


# -------------------- main loop --------------------

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

            force = bool(job.payload.get("force", False))
            n = process_index_version(version_id, pipeline_version, embedding_model_id, force=force)
            mark_done(job.job_id)
            print(
                f"[index_worker] done job_id={job.job_id} version_id={version_id} pipeline={pipeline_version} chunks={n}"
            )

        except Exception as e:
            msg = str(e)

            # Erros "permanentes": retry não adianta, então mata o job.
            # - version_id não existe mais
            # - versão está em status que o worker não aceita (job mal enfileirado)
            permanent = (
                ("version_id não encontrado" in msg)
                or ("status inválido" in msg)
                or ("não possui nodes" in msg)
            )

            if permanent:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE jobs
                            SET status='dead',
                                last_error = concat_ws(E'\n', NULLIF(last_error,''), %s::text),
                                locked_at=NULL,
                                locked_by=NULL,
                                updated_at=now()
                            WHERE job_id=%s
                            """,
                            (msg, job.job_id),
                        )
                    conn.commit()
                print(f"[index_worker] dead job_id={job.job_id}: {msg}")
            else:
                mark_failed(job.job_id, msg, backoff_seconds=15)
                print(f"[index_worker] failed job_id={job.job_id}: {msg}")

        if args.once:
            return


if __name__ == "__main__":
    main()
