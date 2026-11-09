#!/usr/bin/env python3
"""End-to-end demo: patient data → inference engine → conclusions."""
import sys
import os
import json
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.kg.schema import ConceptVocabulary
from src.kg.client import JsonGraphClient
from src.standards.repository import JsonStandardRepository
from src.rules.repository import JsonRuleRepository
from src.terminology.mapper import TerminologyMapper
from src.engine import InferenceEngine
from src.models.patient import PatientSnapshot

DATA_DIR = project_root / "data"
FIXTURES_DIR = project_root / "tests" / "fixtures"


def build_engine() -> InferenceEngine:
    """Build a fully configured inference engine.

    Uses the isolated test fixtures so the demo keeps working after live
    guideline-generated graphs and rules are cleared for re-import.
    """
    vocab = ConceptVocabulary()
    vocab.load(str(DATA_DIR / "vocab" / "concepts.json"))

    kg = JsonGraphClient(str(FIXTURES_DIR / "kg" / "stemi_graph.json"))
    standards = JsonStandardRepository(str(DATA_DIR / "standards"))
    rules = JsonRuleRepository(str(FIXTURES_DIR / "rules"))

    return InferenceEngine(
        rule_repo=rules,
        kg_client=kg,
        standard_repo=standards,
        vocabulary=vocab,
    )


def load_patient(path: str) -> PatientSnapshot:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return PatientSnapshot(**data)


def print_conclusion(c, index: int):
    print(f"\n{'='*70}")
    print(f"  结论 {index + 1}: [{c.rule_id}] {c.disease_name} ({c.disease_code})")
    print(f"  置信度: {c.confidence}  优先级: {c.priority}")
    print(f"  建议: {c.message}")
    if c.suggested_action:
        print(f"  触发动作: {c.suggested_action}")
    if c.guideline_source:
        print(f"  指南来源: {c.guideline_source}")
    if c.evidence:
        print(f"  证据链 ({len(c.evidence)}条):")
        for ev in c.evidence:
            print(f"    ├─ [{ev.evidence_id}] {ev.description}")
            if ev.patient_value_display:
                print(f"    │    患者值: {ev.patient_value_display}")
            if ev.standard_used:
                print(f"    │    判定标准: {ev.standard_used}")
            if ev.source_ref:
                print(f"    │    溯源: {ev.source_ref}")
    print(f"{'='*70}")


def main():
    print("=" * 70)
    print("  CDSS 知识引擎 - STEMI 诊断推理演示")
    print("=" * 70)

    # Build engine
    engine = build_engine()
    print(f"\n✓ 引擎初始化完成")
    print(f"  概念词汇: {len(engine.vocabulary.list_all())} 个")
    print(f"  规则数量: {len(engine.rule_repo.get_active_rules())} 条活跃")

    # Load demo patient
    patient = load_patient(str(DATA_DIR / "patients" / "demo_stemi_patient.json"))
    print(f"\n✓ 患者数据加载完成")
    print(f"  患者: {patient.patient_id} ({patient.age}岁 {patient.gender.value}) @ {patient.hospital_id}")
    print(f"  检验: {len(patient.labs)} 项")
    print(f"  检查发现: {len(patient.exam_findings)} 项")
    print(f"  症状: {len(patient.symptoms)} 项")

    # Run inference
    conclusions = engine.run(patient)
    print(f"\n✓ 推理完成: 匹配 {len(conclusions)} 条规则")

    for i, c in enumerate(conclusions):
        print_conclusion(c, i)

    # Demonstrate hospital-specific standards
    print("\n" + "=" * 70)
    print("  医院标准对照演示: 同一患者数据在不同医院标准的判定差异")
    print("=" * 70)
    patient_fz = patient.model_copy(deep=True)
    patient_fz.hospital_id = "FZ_HOSPITAL"
    conclusions_fz = engine.run(patient_fz)

    myo_evidence = None
    for c in conclusions_fz:
        for ev in c.evidence:
            if ev.lab_code == "MYO" and ev.concept_code == "ELEVATED":
                myo_evidence = ev
                break

    print(f"\n  肌红蛋白判定:")
    print(f"    厦门附一 (XM_FIRST):  阈值 >77 ng/mL → 患者值120 → 匹配 ✓")
    if myo_evidence:
        print(f"    福州某院 (FZ_HOSPITAL): 阈值 >85 ng/mL → {myo_evidence.standard_used}")
    else:
        print(f"    福州某院 (FZ_HOSPITAL): 规则未匹配到MYO相关结论")


if __name__ == "__main__":
    main()

