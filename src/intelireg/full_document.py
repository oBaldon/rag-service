"""Recuperação documental integral dos nós armazenados, sem ranking de chunks."""
import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4

from api.errors import ApiError
from intelireg.db import get_conn
from intelireg.regulatory_identifiers import parse_regulatory_identifier, identifiers_match, _fold


def requested_identifier(question):
    references = re.findall(r"(?:\d{1,3}(?:\.\d{3})+|\d+)\s*/\s*(?:19|20|21)\d{2}\b", question)
    if len(set(references)) > 1:
        raise ApiError(422, 'norm_identifier_required', 'Solicite o resumo de uma norma por vez, informando tipo, número e ano.')
    identifier = parse_regulatory_identifier(question)
    if identifier:
        return identifier, identifier.number, identifier.year
    match = re.search(r"\bnorma\s+(?:n(?:o|umero)?\.?\s*)?(\d{1,3}(?:\.\d{3})+|\d+)\s*/\s*((?:19|20|21)\d{2})\b", _fold(question))
    if not match:
        raise ApiError(422, 'norm_identifier_required', 'Informe tipo, número e ano da norma, por exemplo RDC 327/2019.')
    return None, int(match[1].replace('.', '')), int(match[2])


def retrieve_document(question, request_id, version_id=None):
    identifier, number, year = requested_identifier(question)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Limitar o inventário e falhar em vez de escolher arbitrariamente.
            cur.execute("""
                SELECT DISTINCT ON (d.document_id)
                    d.document_id, d.title, d.source_org, d.doc_type,
                    v.version_id, v.source_url, v.final_url, v.captured_at, v.content_hash
                FROM documents d JOIN document_versions v ON v.document_id=d.document_id
                WHERE (d.title ILIKE %s OR d.title ILIKE %s)
                  AND (%s::text IS NULL OR d.title ILIKE %s)
                  AND (%s::text IS NULL OR d.doc_type = ANY(%s) OR unaccent(d.title) ILIKE unaccent(%s))
                  AND (%s::uuid IS NULL OR v.version_id=%s::uuid)
                ORDER BY d.document_id, v.captured_at DESC NULLS LAST, v.created_at DESC, v.version_id
                LIMIT 501
            """, (f'%{number}%', f'%{number:,}%'.replace(',', '.'), str(year) if year else None, f'%{year}%',
                  identifier.family if identifier else None, list(identifier.doc_types) if identifier else [],
                  f'%{identifier.family}%' if identifier else None, version_id, version_id))
            rows = cur.fetchall()
            if len(rows) > 500:
                raise ApiError(409, 'norm_ambiguous', 'Há muitas candidatas. Informe o tipo e o identificador completo da norma.')
            candidates = []
            for row in rows:
                parsed = parse_regulatory_identifier(row[1])
                if parsed and ((identifier and identifiers_match(identifier, parsed)) or
                               (identifier is None and parsed.number == number and parsed.year == year)):
                    candidates.append(row)
            if not candidates:
                raise ApiError(404, 'norm_not_found', 'A norma não foi localizada no acervo. Confira tipo, número e ano.')
            if len(candidates) > 1:
                by_org = [r for r in candidates if len(r[2]) >= 3 and _fold(r[2]) in _fold(question)]
                if by_org:
                    candidates = by_org
            if len(candidates) != 1:
                labels = '; '.join(f'{r[1]} ({r[2]})' for r in candidates[:8])
                raise ApiError(409, 'norm_ambiguous', 'Especifique qual norma deseja resumir: ' + labels,
                               {'candidates': [{'title': r[1], 'source_org': r[2], 'version_id': str(r[4])} for r in candidates[:8]]})
            row = candidates[0]
            cur.execute('SELECT node_id, node_index, heading_text, text_normalized FROM nodes WHERE version_id=%s ORDER BY node_index, node_id', (row[4],))
            nodes = cur.fetchall()
    # Nós estruturais podem conter apenas heading_text (capítulos/seções).
    # Preservá-los; rejeitar ausência de corpo ou nós inteiramente vazios.
    if not nodes or not any(n[3].strip() for n in nodes) or any(not n[2].strip() and not n[3].strip() for n in nodes):
        raise ApiError(422, 'norm_text_unavailable', 'O texto armazenado está ausente ou contém seções vazias; não é possível resumir a norma completa.')
    indexes = [n[1] for n in nodes]
    if len(set(indexes)) != len(indexes) or indexes != list(range(indexes[0], indexes[0] + len(indexes))):
        raise ApiError(422, 'norm_text_incomplete', 'O texto armazenado apresenta lacunas na sequência das seções.')
    text = '\n\n'.join(f'{n[2]}\n{n[3]}' for n in nodes)
    if len(text) > 300000:
        raise ApiError(413, 'norm_text_too_large', 'A norma excede o limite desta operação. Nenhum trecho foi descartado; solicite resumos por seção.')
    return {
        'schema_version': 1, 'request_id': request_id, 'run_id': str(uuid4()),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'document_id': str(row[0]), 'title': row[1], 'source_org': row[2], 'doc_type': row[3],
        'version_id': str(row[4]), 'source_url': row[5], 'final_url': row[6],
        'captured_at': row[7].isoformat() if row[7] else None,
        'source_content_hash': row[8], 'text': text,
        'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'node_count': len(nodes),
        'coverage': 'all_stored_nodes',
        'limitations': 'Texto normalizado disponível no acervo; completude da publicação oficial e vigência não verificadas.',
    }
