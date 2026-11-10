"""Import and activate the TCM demo candidate rules."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from _smoke_tcm_extraction import CHUNKS
from src.api.routes.rules import RuleImportRequest, activate_rule, import_rules
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import extract_local


async def main() -> None:
    template = ExtractionTemplateRepository().get("TCM_GUIDELINE_V2")
    result = extract_local(CHUNKS, template, "DOC-TCM-DEMO", "中医胸痹心痛病诊疗方案")
    imported = await import_rules(RuleImportRequest(
        rules=result.suggested_rules,
        graph_filename="KG-TCM-DEMO.json",
    ))
    print("import:", imported)

    for rule in result.suggested_rules:
        if rule["rule_type"] != "tcm_pattern":
            continue
        try:
            activated = await activate_rule(rule["rule_id"])
            print("activated:", activated["rule_id"], activated["disease_name"])
        except Exception as exc:
            print("activate failed:", rule["rule_id"], exc)


if __name__ == "__main__":
    asyncio.run(main())
