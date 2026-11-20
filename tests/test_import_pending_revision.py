import copy
from src.review.metadata import apply_import_review_metadata, snapshot_hashes, capture_previous_contents

def test_import_preserves_approved_content_and_stages_revision():
    node = {'id': 'N', 'node_type': 'disease', 'code': 'D', 'name': 'Approved', 'review_status': 'active', 'version': 1}
    graph = {'nodes': {'N': node}, 'relations': {}}
    hashes = snapshot_hashes(graph)
    contents = capture_previous_contents(graph)
    node['name'] = 'Imported change'
    apply_import_review_metadata(graph, hashes, 'DOC-TEST', previous_contents=contents)
    assert node['name'] == 'Approved'
    assert node['review_status'] == 'active'
    assert node['version'] == 1
    assert node['pending_revision']['content']['name'] == 'Imported change'
    assert node['pending_revision']['base_hash'] == hashes['nodes']['N']


def test_revision_decisions_preserve_history_and_reject_stale_requests(tmp_path, monkeypatch):
    import json
    import pytest
    from fastapi import HTTPException
    from src.api.routes import kg_review as api
    from src.review.service import ReviewService
    from tests.test_graph_review_service import GRAPH
    graph = copy.deepcopy(GRAPH)
    node = graph['nodes'][0]
    hashes = snapshot_hashes(graph)
    contents = capture_previous_contents(graph)
    old_name = node['name']
    node['review_status'] = 'active'
    node['name'] = 'New approved version'
    apply_import_review_metadata(graph, hashes, 'DOC-T', previous_contents=contents)
    kg = tmp_path / 'kg'
    kg.mkdir()
    path = kg / 'TEST.json'
    path.write_text(json.dumps(graph))
    rules = tmp_path / 'rules.json'
    rules.write_text('[]')
    monkeypatch.setattr(api, 'service', ReviewService(kg, rules, tmp_path / 'audit.jsonl'))
    pending = api.pending_revisions('TEST.json')['items'][0]
    assert pending['before']['name'] == old_name
    request = api.RevisionDecision(kind='node', item_id=node['id'], action='approve',
                                   reviewer='Reviewer', revision_hash=pending['revision_hash'])
    api.decide_revision(request, 'TEST.json')
    saved = api.service._load_graph('TEST.json')['nodes'][node['id']]
    assert saved['name'] == 'New approved version'
    assert saved['review_status'] == 'active'
    assert saved['revision_history'][-1]['before']['name'] == old_name
    assert not api.pending_revisions('TEST.json')['items']
    with pytest.raises(HTTPException):
        api.decide_revision(request, 'TEST.json')


def test_reject_revision_keeps_approved_content(tmp_path, monkeypatch):
    from src.api.routes import kg_review as api
    from src.review.clinical_basis import digest
    node = {'id': 'N', 'name': 'Approved', 'code': 'D', 'node_type': 'disease', 'review_status': 'active'}
    revision = {'content': {**node, 'name': 'Proposed'}, 'base_hash': snapshot_hashes({'nodes': [node]})['nodes']['N']}
    node['pending_revision'] = revision
    graph = {'nodes': {'N': node}, 'relations': {}}
    class FakeService:
        def _load_graph(self, filename): return graph
        def _load_rules(self): return []
        def _persist(self, *args): pass
    monkeypatch.setattr(api, 'service', FakeService())
    api.decide_revision(api.RevisionDecision(kind='node', item_id='N', action='reject',
                                            reviewer='R', revision_hash=digest(revision)), 'G.json')
    assert node['name'] == 'Approved'
    assert node['review_status'] == 'active'
    assert 'pending_revision' not in node
