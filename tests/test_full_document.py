from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
import hashlib
import pytest

from api.errors import ApiError
from intelireg import full_document as service


def document(title='Resolução RDC 327/2019', org='ANVISA'):
    return (uuid4(), title, org, 'rdc', uuid4(), 'https://example.test/source', None, datetime.now(timezone.utc), 'source-hash')


def fake_db(monkeypatch, documents, nodes):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params): self.rows = nodes if 'SELECT node_id' in sql else documents
        def fetchall(self): return self.rows
    class Connection:
        def cursor(self): return Cursor()
    @contextmanager
    def connect(): yield Connection()
    monkeypatch.setattr(service, 'get_conn', connect)


def test_document_uses_all_nodes_in_order_without_embedding_retrieval(monkeypatch):
    doc = document()
    nodes = [(uuid4(), i, f'Art. {i+1}', f'Texto literal {i+1}') for i in range(4)]
    fake_db(monkeypatch, [doc], nodes)
    result = service.retrieve_document('Resuma a RDC 327/2019', 'request-1')
    assert result['node_count'] == 4
    assert result['version_id'] == str(doc[4])
    assert result['text'].endswith('Texto literal 4')
    assert result['text_sha256'] == hashlib.sha256(result['text'].encode()).hexdigest()
    assert result['coverage'] == 'all_stored_nodes'


def test_generic_norm_requires_disambiguation_across_families(monkeypatch):
    fake_db(monkeypatch, [document('RDC 1015/2015'), document('Portaria 1.015/2015')], [])
    with pytest.raises(ApiError) as error:
        service.retrieve_document('Resuma a norma 1.015/2015', 'r')
    assert error.value.code == 'norm_ambiguous'
    assert len(error.value.details['candidates']) == 2


def test_specific_type_filters_other_families(monkeypatch):
    fake_db(monkeypatch, [document('RDC 1015/2015'), document('Portaria 1.015/2015')], [(uuid4(), 0, 'Art. 1', 'Texto')])
    assert service.retrieve_document('Resuma a RDC 1015/2015', 'r')['title'] == 'RDC 1015/2015'


@pytest.mark.parametrize('nodes,code', [([], 'norm_text_unavailable'), ([(uuid4(), 0, 'Artigo', '')], 'norm_text_unavailable'), ([(uuid4(), 0, 'A', 'a'), (uuid4(), 2, 'B', 'b')], 'norm_text_incomplete')])
def test_missing_or_gapped_text_is_not_summarized(monkeypatch, nodes, code):
    fake_db(monkeypatch, [document()], nodes)
    with pytest.raises(ApiError) as error:
        service.retrieve_document('RDC 327/2019', 'r')
    assert error.value.code == code


def test_not_found_and_multiple_requests(monkeypatch):
    fake_db(monkeypatch, [], [])
    with pytest.raises(ApiError) as error:
        service.retrieve_document('RDC 327/2019', 'r')
    assert error.value.code == 'norm_not_found'
    with pytest.raises(ApiError):
        service.requested_identifier('Resuma RDC 327/2019 e RDC 15/2020')


def test_endpoint_auth_and_document_contract(client, monkeypatch):
    fake_db(monkeypatch, [document()], [(uuid4(), 0, 'Art. 1', 'Texto')])
    response = client.post('/v1/rag/document', json={'question': 'Resuma RDC 327/2019'}, headers={'X-Request-Id': 'doc-test'})
    assert response.status_code == 200
    assert response.json()['request_id'] == 'doc-test'
    from intelireg import settings
    monkeypatch.setattr(settings, 'RAG_API_KEY', 'required')
    assert client.post('/v1/rag/document', json={'question': 'RDC 327/2019'}).status_code == 401


def test_stored_anvisa_title_resolves_only_for_the_requested_year(monkeypatch):
    fake_db(monkeypatch, [document('Resolução da Diretoria Colegiada Anvisa no 1.015, de 02/02/2026')], [(uuid4(), 0, 'Art. 1', 'Texto')])
    assert service.retrieve_document('Resuma a norma 1.015/2026', 'r')['node_count'] == 1
    with pytest.raises(ApiError) as error:
        service.retrieve_document('Resuma a norma 1.015/2015', 'r')
    assert error.value.code == 'norm_not_found'


def test_structural_headings_without_own_body_are_preserved(monkeypatch):
    fake_db(monkeypatch, [document()], [(uuid4(), 0, 'CAPÍTULO I', ''), (uuid4(), 1, 'Art. 1', 'Obrigação literal'), (uuid4(), 2, 'DIRETORIA COLEGIADA', '')])
    result = service.retrieve_document('Resuma RDC 327/2019', 'r')
    assert result['node_count'] == 3
    assert 'CAPÍTULO I' in result['text']
    assert 'Obrigação literal' in result['text']
    assert 'DIRETORIA COLEGIADA' in result['text']
