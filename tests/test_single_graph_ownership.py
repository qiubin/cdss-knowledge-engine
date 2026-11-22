import json
from pathlib import Path

from src.api.routes import guide_packages


def _write_graph(root: Path, name: str, doc_id: str, shared: bool = False) -> Path:
    path = root / "data" / "kg" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    docs = [doc_id, "DOC-SHARED"] if shared else [doc_id]
    path.write_text(json.dumps({
        "domain": "tcm", "schema_version": 2,
        "nodes": [{
            "id": "ENTITY:disease:D1", "node_type": "disease", "code": "D1",
            "source_doc_ids": docs,
            "source_refs": [f"{doc_id}_p1_l1-2"] + (["DOC-SHARED_p1_l1-2"] if shared else []),
        }],
        "relations": [],
        "sources": [{"doc_id": doc_id, "chunk_id": f"{doc_id}_p1_l1-2"}],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_document_location_scan_detects_cross_graph_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(guide_packages, "ROOT", tmp_path)
    _write_graph(tmp_path, "one.json", "DOC-1")
    _write_graph(tmp_path, "two.json", "DOC-1")
    assert guide_packages._graph_doc_locations("DOC-1") == ["one.json", "two.json"]
    assert guide_packages._graph_doc_locations("DOC-1", exclude="two.json") == ["one.json"]


def test_migration_removes_only_old_document_contribution(tmp_path, monkeypatch):
    monkeypatch.setattr(guide_packages, "ROOT", tmp_path)
    path = _write_graph(tmp_path, "old.json", "DOC-1", shared=True)
    guide_packages._remove_doc_from_graph("DOC-1", "old.json")
    graph = json.loads(path.read_text(encoding="utf-8"))
    assert graph["nodes"][0]["source_doc_ids"] == ["DOC-SHARED"]
    assert graph["nodes"][0]["source_refs"] == ["DOC-SHARED_p1_l1-2"]
    assert graph["sources"] == []


def test_migration_deletes_document_only_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(guide_packages, "ROOT", tmp_path)
    path = _write_graph(tmp_path, "old.json", "DOC-1")
    guide_packages._remove_doc_from_graph("DOC-1", "old.json")
    graph = json.loads(path.read_text(encoding="utf-8"))
    assert graph["nodes"] == []
