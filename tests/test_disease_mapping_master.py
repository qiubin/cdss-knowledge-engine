import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.api.routes import kg as kg_routes
from src.vocab.disease_mapping import DiseaseMappingRepository


def _master(path: Path, status: str = "active") -> None:
    path.write_text(json.dumps({"mappings": [{
        "mapping_id": "MAP-001",
        "tcm_disease_code": "TCM_DIS_XIN_SHUAI",
        "tcm_disease_name": "心衰",
        "western_disease_code": "I50.9",
        "western_disease_name": "心力衰竭",
        "mapping_type": "related",
        "source": "reviewed master data",
        "review_status": status,
    }]}, ensure_ascii=False), encoding="utf-8")


def test_repository_only_returns_reviewed_active_rows():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "mappings.json"
        _master(path, "draft")
        assert DiseaseMappingRepository(path).list()
        assert DiseaseMappingRepository(path).list(active_only=True) == []


def test_graph_separates_guideline_candidate_from_formal_master_mapping():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        mapping_path = root / "mappings.json"
        _master(mapping_path)
        chunk = {"chunk_id": "c1", "page_start": 1, "page_end": 1,
                 "line_start": 1, "line_end": 1, "text": "心衰属于心力衰竭范畴。"}
        request = kg_routes.GraphImportRequest(
            doc_id="D1", doc_title="test", domain="tcm", graph_filename="mapping.json",
            chunks=[chunk],
            entities=[
                {"entity_type": "disease", "code": "TCM_DIS_XIN_SHUAI", "name": "心衰", "chunk_id": "c1"},
                {"entity_type": "western_disease", "code": "I50.9", "name": "心力衰竭", "chunk_id": "c1"},
            ],
            suggested_relations=[{
                "relation_code": "DISEASE_MAPS_TO_WESTERN", "source_type": "disease",
                "source_code": "TCM_DIS_XIN_SHUAI", "target_type": "western_disease",
                "target_code": "I50.9", "chunk_id": "c1", "quote": "心衰属于心力衰竭范畴。",
            }],
        )
        with patch.object(kg_routes, "KG_DIR", root), patch.object(
            kg_routes, "DISEASE_MAPPING_PATH", mapping_path
        ):
            asyncio.run(kg_routes.import_graph(request))
        graph = json.loads((root / "mapping.json").read_text(encoding="utf-8"))
        relations = {row["relation_code"]: row for row in graph["relations"]}
        assert relations["GUIDELINE_SUGGESTS_DISEASE_MAPPING"]["attributes"]["formal_mapping"] is False
        formal = relations["DISEASE_MAPS_TO_WESTERN"]
        assert formal["attributes"]["mapping_origin"] == "master_data"
        assert formal["review_status"] == "active"
