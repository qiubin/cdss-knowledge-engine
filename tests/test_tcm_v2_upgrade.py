"""Regression coverage for the TCM V2 extraction, scoring and test workbench."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.routes.inference import _evaluate_expected
from src.api.routes import guide_packages
from src.domains import get_profile
from src.domains.tcm.checks import TcmGraphSemanticChecker
from src.engine import InferenceEngine
from src.extraction.repository import ExtractionTemplateRepository
from src.extraction.service import extract_local
from src.kg.client import JsonGraphClient
from src.kg.schema import ConceptVocabulary
from src.models.patient import PatientSnapshot
from src.models.rule import Rule
from src.rules.repository import JsonRuleRepository
from src.standards.repository import JsonStandardRepository


CHUNKS = [
    {
        "chunk_id": "c1", "section": "辨证", "page_start": 1, "page_end": 1,
        "line_start": 1, "line_end": 3,
        "text": "气阴两虚证：胸闷、气短、乏力，舌淡红，脉细弱。治法：益气养阴。方剂：生脉散，组成：人参9克、麦冬9克、五味子6克。",
    },
    {
        "chunk_id": "c2", "section": "兼证", "page_start": 1, "page_end": 1,
        "line_start": 4, "line_end": 6,
        "text": "痰浊闭阻证：痰多、肢体沉重，舌淡胖，脉弦滑。",
    },
]


def patient() -> PatientSnapshot:
    payload = json.loads(
        (project_root / "data" / "patients" / "demo_tcm_patient.json").read_text(
            encoding="utf-8"
        )
    )
    return PatientSnapshot(**payload)


def node(entity: dict) -> dict:
    return {
        "id": f"ENTITY:{entity['entity_type']}:{entity['code']}",
        "node_type": entity["entity_type"],
        "code": entity["code"],
        "name": entity.get("name") or entity["code"],
        "review_status": "active",
        "attributes": entity.get("attributes") or {},
    }


class TcmV2UpgradeTest(unittest.TestCase):
    def test_profile_uses_v2_contract(self):
        profile = get_profile("tcm")
        self.assertEqual(profile.default_template_id, "TCM_GUIDELINE_V2")
        expected = {
            "constitution", "etiology", "syndrome_element", "meridian",
            "acupoint", "external_therapy", "care_guidance", "western_disease",
        }
        self.assertTrue(expected <= set(profile.graph.allowed_entity_types))
        self.assertIn("disease->western_disease", [item["spec"] for item in profile.path_panels])

    def test_graph_target_is_domain_and_schema_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = guide_packages.ROOT
            guide_packages.ROOT = root
            self.addCleanup(setattr, guide_packages, "ROOT", original_root)

            default = guide_packages._resolve_graph_target(
                None, "tcm", "TCM_GUIDELINE_V2", "DOC-TCM-2026"
            )
            self.assertEqual(default, {
                "mode": "new",
                "filename": "KG-TCM-DOC-TCM-2026.json",
                "domain": "tcm",
            })

            tcm_graph = {
                "domain": "tcm", "schema_version": 2,
                "nodes": [], "relations": [],
            }
            graph_path = root / "data" / "kg" / "TCM-EXISTING.json"
            graph_path.parent.mkdir(parents=True)
            graph_path.write_text(json.dumps(tcm_graph), encoding="utf-8")
            existing = guide_packages._resolve_graph_target(
                {"mode": "existing", "filename": "TCM-EXISTING.json"},
                "tcm", "TCM_GUIDELINE_V2", "DOC-TCM-2026",
            )
            self.assertEqual(existing["filename"], "TCM-EXISTING.json")

            with self.assertRaises(Exception):
                guide_packages._resolve_graph_target(
                    {"mode": "new", "filename": "TCM-EXISTING.json"},
                    "western", "GENERIC_GUIDELINE_V1", "DOC-W",
                )
            graph_path.write_text(json.dumps(
                {**tcm_graph, "source": "builtin-demo"}, ensure_ascii=False
            ), encoding="utf-8")
            with self.assertRaises(Exception):
                guide_packages._resolve_graph_target(
                    {"mode": "existing", "filename": "TCM-EXISTING.json"},
                    "tcm", "TCM_GUIDELINE_V2", "DOC-TCM-2026",
                )
            graph_path.write_text(json.dumps({**tcm_graph, "schema_version": 1}), encoding="utf-8")
            with self.assertRaises(Exception):
                guide_packages._resolve_graph_target(
                    {"mode": "existing", "filename": "TCM-EXISTING.json"},
                    "tcm", "TCM_GUIDELINE_V2", "DOC-TCM-2026",
                )

    def test_v2_rules_use_weighted_conditions(self):
        template = ExtractionTemplateRepository().get("TCM_GUIDELINE_V2")
        result = extract_local(CHUNKS, template, "DOC-TCM-V2", "中医 V2 指南")
        pattern = next(
            rule for rule in result.suggested_rules
            if rule["rule_type"] == "tcm_pattern"
            and rule["disease_code"] == "TCM_SYN_QI_YIN_XU"
        )
        Rule(**pattern)
        conditions = pattern["conditions"]
        self.assertEqual(conditions["type"], "weighted_group")
        self.assertEqual(conditions["scoring"], "normalized_weight_sum")
        self.assertEqual(conditions["threshold"], 0.6)
        self.assertTrue(conditions["parameter"]["require_tongue_or_pulse"])
        weights = {
            child["code"]: child["weight"]
            for child in conditions["children"]
            if child["type"] == "observation"
        }
        self.assertEqual(weights["SYM_SHORTNESS_BREATH"], 3.0)
        self.assertEqual(weights["TCM_TONGUE_PALE_RED"], 2.0)
        self.assertEqual(weights["TCM_PULSE_THREADY_WEAK"], 2.0)

    def test_semantic_checker_walks_weighted_group(self):
        rule = {
            "rule_id": "R1", "rule_type": "tcm_pattern", "status": "draft",
            "anchor_type": "syndrome", "disease_code": "TCM_SYN_QI_YIN_XU",
            "conditions": {
                "type": "weighted_group",
                "children": [{
                    "type": "observation", "observation_type": "symptom",
                    "code": "SYM_FATIGUE",
                }],
                "required_children": [{
                    "type": "group", "operator": "OR", "children": [{
                        "type": "observation", "observation_type": "symptom",
                        "code": "SYM_CHEST_TIGHTNESS",
                    }],
                }],
            },
        }
        graph = {"nodes": [node({"entity_type": "syndrome", "code": "TCM_SYN_QI_YIN_XU"})]}
        result = TcmGraphSemanticChecker(graph).check_rule(rule)
        self.assertEqual(result["semantic_status"], "inconsistent")
        self.assertFalse(any("条件类型未接入" in issue for issue in result["issues"]))

    def test_weighted_engine_and_candidate_review(self):
        template = ExtractionTemplateRepository().get("TCM_GUIDELINE_V2")
        extraction = extract_local(CHUNKS, template, "DOC-TCM-V2", "中医 V2 指南")
        nodes = []
        seen = set()
        for entity in extraction.entities:
            key = (entity["entity_type"], entity["code"])
            if key not in seen:
                seen.add(key)
                nodes.append(node(entity))
        graph = {
            "domain": "tcm", "schema_version": 2, "nodes": nodes,
            "relations": [], "syndromes": [], "diseases": [], "evidences": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "kg").mkdir()
            (root / "rules").mkdir()
            (root / "standards").mkdir()
            graph_path = root / "kg" / "KG-TCM-V2.json"
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            rules = [dict(rule) for rule in extraction.suggested_rules]
            for rule in rules:
                rule["graph_filename"] = "KG-TCM-V2.json"
                if rule["rule_type"] == "tcm_pattern":
                    rule["status"] = "active" if rule["disease_code"] == "TCM_SYN_QI_YIN_XU" else "draft"
                else:
                    rule["status"] = "draft"
            (root / "rules" / "managed_rules.json").write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8"
            )
            vocab = ConceptVocabulary()
            vocab.load(str(project_root / "data" / "vocab" / "concepts.json"))
            engine = InferenceEngine(
                JsonRuleRepository(str(root / "rules")),
                JsonGraphClient(str(graph_path)),
                JsonStandardRepository(str(root / "standards")),
                vocab,
                lambda filename: JsonGraphClient(str(root / "kg" / filename)),
                domain="tcm",
            )
            conclusions = engine.run(patient(), include_draft_patterns=True)
            types = {item.rule_type for item in conclusions}
            self.assertEqual(
                types,
                {"tcm_pattern", "tcm_treatment", "tcm_prescription"},
            )
            pattern = next(item for item in conclusions if item.rule_type == "tcm_pattern")
            self.assertGreater(pattern.score or 0, 0.8)
            self.assertGreater(pattern.score_breakdown["categories"]["symptom"]["score"], 0.8)
            self.assertTrue(pattern.matched_items)
            self.assertFalse(pattern.missing_items)

            candidates = engine.evaluate_tcm_pattern_candidates(patient())
            self.assertEqual(
                [item["matched"] for item in candidates],
                [True, False],
            )
            self.assertIn("categories", candidates[-1]["conclusion"]["score_breakdown"])

    def test_expected_assertions_report_failure_causes(self):
        candidates = [
            {
                "matched": False,
                "conclusion": {
                    "rule_type": "tcm_pattern", "anchor_code": "TCM_SYN_QI_YIN_XU",
                    "anchor_name": "气阴两虚证", "score": 0.55, "priority": 50,
                },
            },
            {
                "matched": True,
                "conclusion": {
                    "rule_type": "tcm_pattern", "anchor_code": "TCM_SYN_TAN_ZHUO_BI_ZU",
                    "anchor_name": "痰浊闭阻证", "score": 0.8, "priority": 50,
                },
            },
        ]
        checks = _evaluate_expected({
            "top_syndrome": "TCM_SYN_QI_YIN_XU",
            "min_score": 0.8,
            "must_include_formula": "TCM_FORMULA_SHENGMAI_SAN",
            "must_not_trigger": ["TCM_SYN_TAN_ZHUO_BI_ZU"],
        }, candidates, [])
        by_id = {item["id"]: item for item in checks}
        self.assertFalse(by_id["top_syndrome"]["passed"])
        self.assertFalse(by_id["min_score"]["passed"])
        self.assertFalse(by_id["must_include_formula"]["passed"])
        self.assertFalse(by_id["must_not_trigger"]["passed"])


if __name__ == "__main__":
    unittest.main()
