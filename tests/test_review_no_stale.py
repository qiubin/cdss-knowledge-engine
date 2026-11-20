from pathlib import Path
from src.review.status import GRAPH_STATUSES, normalize_legacy_review_statuses, next_graph_status
from src.review.metadata import apply_import_review_metadata, snapshot_hashes, capture_previous_contents


def test_legacy_pending_is_draft_and_approved_metadata_survives():
    graph = {'nodes': [{'review_status':'stale','reviewed_by':'医生','reviewed_at':'2026-11-20'},
                       {'review_status':'active'}], 'clinical_bases':[{'status':'stale'}]}
    assert normalize_legacy_review_statuses(graph) == 2
    assert graph['nodes'][0]['review_status'] == 'draft'
    assert graph['nodes'][0]['reviewed_by'] == '医生'
    assert graph['nodes'][1]['review_status'] == 'active'
    assert 'stale' not in GRAPH_STATUSES
    assert next_graph_status('stale','approve') == 'active'


def test_import_keeps_approved_version_and_stages_changes():
    graph={'nodes':[{'id':'N','node_type':'disease','name':'已通过','code':'D','review_status':'active'}],'relations':[]}
    hashes=snapshot_hashes(graph); old=capture_previous_contents(graph)
    graph['nodes'][0]['name']='新名称'
    apply_import_review_metadata(graph,hashes,'DOC-T',previous_contents=old)
    assert graph['nodes'][0]['review_status']=='active'
    assert graph['nodes'][0]['name']=='已通过'
    assert graph['nodes'][0]['pending_revision']['content']['name']=='新名称'


def test_review_ui_has_no_separate_stale_entry():
    app=Path('src/api/static/app.js').read_text()
    assert '待复核' not in app
    assert '["stale", "历史' not in app
    assert '{ value: "stale"' not in app
