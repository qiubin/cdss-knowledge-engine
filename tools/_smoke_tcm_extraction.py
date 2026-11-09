"""Smoke test for TCM template extraction and rule generation."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.extraction.repository import ExtractionTemplateRepository
from src.models.rule import Rule
from src.extraction.service import extract_local


def chunk(chunk_id: str, text: str, page: int = 1) -> dict:
    lines = text.split("\n")
    return {
        "chunk_id": chunk_id,
        "section": chunk_id,
        "page_start": page,
        "page_end": page,
        "line_start": 1,
        "line_end": len(lines),
        "text": text,
        "page_numbers": [page],
        "line_numbers": list(range(1, len(lines) + 1)),
    }


CHUNKS = [
    chunk("c1", "胸痹心痛病\n气阴两虚证：胸闷、气短、乏力、自汗，舌淡红，苔薄白，脉细弱。治法：益气养阴。方药：生脉散（人参10g、麦冬15g、五味子6g）。"),
    chunk("c2", "胸痹心痛病\n痰浊闭阻证：胸闷、痰多、肢体沉重，舌淡胖，苔白腻，脉弦滑。治法：通阳泄浊豁痰。方药：瓜蒌薤白半夏汤（瓜蒌15g、薤白12g、半夏9g）。", page=2),
    chunk("c3", "胸痹心痛病\n心血瘀阻证：胸痛、心悸、唇甲紫暗，舌紫暗，脉涩。治法：活血化瘀通络。方药：血府逐瘀汤（桃仁12g、红花9g、当归9g、川芎6g、赤芍6g、柴胡3g、枳壳6g、桔梗6g、牛膝9g、甘草6g）。", page=3),
]


def main() -> None:
    template = ExtractionTemplateRepository().get("TCM_GUIDELINE_V1")
    result = extract_local(CHUNKS, template, "DOC-TCM-TEST", "中医测试指南")

    print("entities:", Counter(item["entity_type"] for item in result.entities))
    print("relations:", Counter(item["relation_code"] for item in result.suggested_relations))
    print("rules:", Counter(item["rule_type"] for item in result.suggested_rules))
    for rule in result.suggested_rules:
        Rule(**rule)
    for rule in result.suggested_rules:
        if rule["rule_type"] == "tcm_prescription":
            print("prescription:", rule["disease_name"], rule["conclusion"]["formula_name"],
                  [herb["name"] for herb in rule["conclusion"]["herbs"]][:4])
    print("warnings:", result.warnings)
    print("OK")


if __name__ == "__main__":
    main()
