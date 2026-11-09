"""Seed the TCM demo graph from the smoke guideline chunks."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from _smoke_tcm_extraction import CHUNKS
from src.api.routes.kg import GraphImportRequest, import_graph
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import extract_local


async def main() -> None:
    template = ExtractionTemplateRepository().get("TCM_GUIDELINE_V1")
    result = extract_local(CHUNKS, template, "DOC-TCM-DEMO", "中医胸痹心痛病诊疗方案")
    response = await import_graph(GraphImportRequest(
        doc_id="DOC-TCM-DEMO",
        doc_title="中医胸痹心痛病诊疗方案",
        domain="tcm",
        target_disease_code="TCM_SYN_QI_YIN_XU",
        entities=result.entities,
        chunks=CHUNKS,
        suggested_relations=result.suggested_relations,
        template_id="TCM_GUIDELINE_V1",
        graph_filename="tcm_graph.json",
        candidate_rules=result.suggested_rules,
    ))
    print("import:", response)


if __name__ == "__main__":
    asyncio.run(main())
