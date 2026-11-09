"""Rebuild the uploaded myocarditis guideline with the v2 rule generator.

This is an operational repair script: it re-extracts the already-parsed
document, replaces its graph/rules/package, then bulk-activates rules that pass
graph sync and quality checks.  It does NOT re-upload the PDF.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api.routes.guide_packages import (
    GuidePackageGenerateRequest,
    GuidePackageImportRequest,
    generate_package,
    import_package,
)
from src.api.routes.rules import ActivateConsistentRequest, activate_consistent
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import extract_local

DOC_ID = "DOC-76845efb"
KG_NAME = "KG-GENERIC_GUIDELINE_V1.json"
DOC_PATH = ROOT / "data" / "uploads" / f"{DOC_ID}.parsed.json"
KG_PATH = ROOT / "data" / "kg" / KG_NAME
RULES_PATH = ROOT / "data" / "rules" / "managed_rules.json"


def clear_graph() -> None:
    KG_PATH.write_text(json.dumps({
        "schema_version": 1,
        "domain": "western",
        "anchor_types": ["disease"],
        "diseases": [],
        "evidences": [],
        "disease_evidence": [],
        "sources": [],
        "nodes": [],
        "relations": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def prune_doc_rules() -> None:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    kept = [
        item for item in rules
        if not str(item.get("rule_id") or "").startswith(f"{DOC_ID}-")
    ]
    RULES_PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"pruned rules: {len(rules)} -> {len(kept)}")


async def rebuild() -> None:
    doc = json.loads(DOC_PATH.read_text(encoding="utf-8"))
    template = ExtractionTemplateRepository().get("GENERIC_GUIDELINE_V1")
    result = extract_local(
        doc["parsed"]["chunks"], template, doc["doc_id"], doc["doc_title"]
    )
    doc["entities"] = result.entities
    doc["suggested_relations"] = result.suggested_relations
    doc["suggested_rules"] = result.suggested_rules
    doc.setdefault("extraction", {})
    doc["extraction"].update({
        "domain": "western",
        "template_id": template.template_id,
        "template_name": template.name,
        "graph_filename": KG_NAME,
        "warnings": result.warnings,
        "metadata": result.metadata,
    })
    DOC_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "re-extracted entities:", len(result.entities),
        "rules:", len(result.suggested_rules),
        "types:", {item["rule_type"] for item in result.suggested_rules},
    )

    clear_graph()
    prune_doc_rules()

    package_response = await generate_package(GuidePackageGenerateRequest(
        doc_id=DOC_ID,
        domain="western",
        template_id="GENERIC_GUIDELINE_V1",
        status="reviewed",
    ))
    print("package checks:", [
        (item["code"], item["status"], len(item.get("issues", [])))
        for item in package_response["package"]["checks"]
    ])
    import_response = await import_package(
        package_response["package"]["package_id"],
        GuidePackageImportRequest(reviewed_only=False),
    )
    print("import status:", import_response["status"])
    print("rule import:", {
        key: import_response["rules"].get(key)
        for key in ("imported", "replaced", "errors")
    })

    activation = await activate_consistent(ActivateConsistentRequest(
        domain="western",
        quality_only=True,
    ))
    print("activation:", activation)


if __name__ == "__main__":
    asyncio.run(rebuild())
