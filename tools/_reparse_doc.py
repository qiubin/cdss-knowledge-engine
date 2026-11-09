import asyncio
import json
import logging
import sys
from pathlib import Path

from src.api.routes.pdf import (
    _doc_store,
    _load_doc,
    _now,
    _persist_doc,
    extraction_config,
    extraction_templates,
    parse_pdf,
    WARN_NO_TEXT_LAYER,
)
from src.extraction.service import extract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("uvicorn.error").setLevel(logging.INFO)

doc_id = sys.argv[1] if len(sys.argv) > 1 else "DOC-ead50a9c"
record = _load_doc(doc_id)
if not record:
    raise SystemExit(f"Document not found: {doc_id}")

doc_title = record.get("doc_title") or doc_id
file_path = Path(record["file_path"])
logging.info("Re-parsing %s: %s", doc_id, file_path)
parsed = parse_pdf(file_path, doc_id)

config = extraction_config.load()
mode = (config.get("default_mode") or "local").lower()
template = extraction_templates.get(config.get("default_template_id") or "")
provider_result = asyncio.run(
    extract(mode, parsed["chunks"], template, doc_id, doc_title, config)
)

extra_warnings = [WARN_NO_TEXT_LAYER] if parsed.get("ocr_used") else []
new_record = {
    "doc_id": doc_id,
    "doc_title": doc_title,
    "file_path": str(file_path),
    "parsed": parsed,
    "entities": provider_result.entities,
    "suggested_relations": provider_result.suggested_relations,
    "suggested_rules": provider_result.suggested_rules,
    "extraction": {
        "mode": mode,
        "template_id": template.template_id,
        "template_name": template.name,
        "graph_filename": template.graph_filename,
        "warnings": [*provider_result.warnings, *extra_warnings],
        "metadata": provider_result.metadata,
    },
    "uploaded_at": _now(),
}
_doc_store[doc_id] = new_record
_persist_doc(new_record)

summary = {
    "doc_id": doc_id,
    "total_pages": parsed.get("total_pages"),
    "total_lines": parsed.get("total_lines"),
    "chunks": len(parsed.get("chunks") or []),
    "entities": len(provider_result.entities),
    "relations": len(provider_result.suggested_relations),
    "rules": len(provider_result.suggested_rules),
    "ocr_used": bool(parsed.get("ocr_used")),
    "template_id": template.template_id,
}
print(json.dumps(summary, ensure_ascii=False))
