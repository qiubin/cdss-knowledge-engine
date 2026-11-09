#!/usr/bin/env python3
"""Complete hospital lab interpretation standards without overwriting existing rows."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
STANDARDS_DIR = ROOT / "data" / "standards"


def _row(
    hospital_id: str,
    lab_code: str,
    lab_name: str,
    concept_code: str,
    *,
    operator: str | None = None,
    threshold_value: float | None = None,
    threshold_unit: str = "",
    uln_multiplier: float | None = None,
    effective_from: str,
    source_note: str = "",
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "hospital_id": hospital_id,
        "lab_code": lab_code,
        "lab_name": lab_name,
        "concept_code": concept_code,
        "ref_source": "STATIC",
        "effective_from": effective_from,
        "version": 1,
        "status": "active",
    }
    if operator:
        row["operator"] = operator
    if threshold_value is not None:
        row["threshold_value"] = threshold_value
    if threshold_unit:
        row["threshold_unit"] = threshold_unit
    if uln_multiplier is not None:
        row["uln_multiplier"] = uln_multiplier
    if source_note:
        row["source_note"] = source_note
    return row


def _hospital_rows(
    hospital_id: str,
    *,
    effective_from: str,
    ctn_uln: float,
    myo_uln: float,
    myo_low: float,
    ckmb_uln: float,
) -> List[Dict[str, Any]]:
    note = "通用成人参考范围（演示数据），请医院检验科复核"
    panel_note = "组合检验项目，按组成项结果综合判定；请在医院标准中细化"
    rows = [
        _row(hospital_id, "CTN", "肌钙蛋白", "ELEVATED", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL",
             effective_from=effective_from,
             source_note=f"cTnI 参考上限={ctn_uln:g} ng/mL；ELEVATED 与超 ULN 同义"),
        _row(hospital_id, "CTN", "肌钙蛋白", "ABNORMAL", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CTN", "肌钙蛋白", "ABOVE_ULN_5", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL", uln_multiplier=5.0,
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CTN", "肌钙蛋白", "POSITIVE",
             effective_from=effective_from, source_note="定性阳性，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "CTN", "肌钙蛋白", "NEGATIVE",
             effective_from=effective_from, source_note="定性阴性，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "ELEVATED", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL",
             effective_from=effective_from,
             source_note="TROPONIN 为 CTN 别名编码，使用同一参考上限"),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "ABNORMAL", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "ABOVE_ULN_1", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL", uln_multiplier=1.0,
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "ABOVE_ULN_5", operator=">",
             threshold_value=ctn_uln, threshold_unit="ng/mL", uln_multiplier=5.0,
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "POSITIVE",
             effective_from=effective_from, source_note="定性阳性，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "NEGATIVE",
             effective_from=effective_from, source_note="定性阴性，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "TROPONIN", "肌钙蛋白（TROPONIN 别名）", "DYNAMIC_RISE",
             effective_from=effective_from, source_note="需至少 2 次连续测量"),
        _row(hospital_id, "MYO", "肌红蛋白", "ABOVE_ULN_1", operator=">",
             threshold_value=myo_uln, threshold_unit="ng/mL", uln_multiplier=1.0,
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "MYO", "肌红蛋白", "DYNAMIC_RISE",
             effective_from=effective_from, source_note="需至少 2 次连续测量"),
        _row(hospital_id, "CK_MB", "肌酸激酶同工酶", "ELEVATED", operator=">",
             threshold_value=ckmb_uln, threshold_unit="ng/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CK_MB", "肌酸激酶同工酶", "DYNAMIC_RISE",
             effective_from=effective_from, source_note="需至少 2 次连续测量"),
        _row(hospital_id, "BNP", "B型钠尿肽", "ELEVATED", operator=">",
             threshold_value=100.0, threshold_unit="pg/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "BNP", "B型钠尿肽", "ABNORMAL", operator=">",
             threshold_value=100.0, threshold_unit="pg/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CRP", "C反应蛋白", "ELEVATED", operator=">",
             threshold_value=10.0, threshold_unit="mg/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CRP", "C反应蛋白", "ABNORMAL", operator=">",
             threshold_value=10.0, threshold_unit="mg/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "LDH", "乳酸脱氢酶", "ELEVATED", operator=">",
             threshold_value=250.0, threshold_unit="U/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CREAT", "肌酐", "ELEVATED", operator=">",
             threshold_value=111.0, threshold_unit="umol/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "D_DIMER", "D-二聚体", "ELEVATED", operator=">",
             threshold_value=0.5, threshold_unit="mg/L FEU",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "LACTATE", "乳酸", "ELEVATED", operator=">",
             threshold_value=2.2, threshold_unit="mmol/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "GLUCOSE", "血糖", "ELEVATED", operator=">",
             threshold_value=7.0, threshold_unit="mmol/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "GLUCOSE", "血糖", "ABNORMAL", operator=">",
             threshold_value=7.0, threshold_unit="mmol/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "HBA1C", "糖化血红蛋白", "ELEVATED", operator=">",
             threshold_value=6.5, threshold_unit="%",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "HBA1C", "糖化血红蛋白", "ABNORMAL", operator=">",
             threshold_value=6.5, threshold_unit="%",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "ESR", "血沉", "ELEVATED", operator=">",
             threshold_value=20.0, threshold_unit="mm/h",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "ALBUMIN", "白蛋白", "DECREASED", operator="<",
             threshold_value=35.0, threshold_unit="g/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "MYO", "肌红蛋白", "DECREASED", operator="<",
             threshold_value=myo_low, threshold_unit="ng/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "CBC", "血常规", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "LIVER_FUNCTION", "肝功能", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "RENAL_FUNCTION", "肾功能", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "ELECTROLYTE", "电解质", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "BETA2_MICROGLOBULIN", "β2微球蛋白", "ELEVATED", operator=">",
             threshold_value=2.5, threshold_unit="mg/L",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "FERRITIN", "铁蛋白", "ELEVATED", operator=">",
             threshold_value=300.0, threshold_unit="ng/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "FERRITIN", "铁蛋白", "DECREASED", operator="<",
             threshold_value=15.0, threshold_unit="ng/mL",
             effective_from=effective_from, source_note=note),
        _row(hospital_id, "COAGULATION", "凝血功能", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "LIPID_PANEL", "血脂", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "THYROID_FUNCTION", "甲状腺功能", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "HBV_SEROLOGY", "乙肝血清学", "POSITIVE",
             effective_from=effective_from, source_note="定性结果，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "HBV_SEROLOGY", "乙肝血清学", "NEGATIVE",
             effective_from=effective_from, source_note="定性结果，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "HIV_SEROLOGY", "HIV检测", "POSITIVE",
             effective_from=effective_from, source_note="筛查阳性需按规范复检确认"),
        _row(hospital_id, "HIV_SEROLOGY", "HIV检测", "NEGATIVE",
             effective_from=effective_from, source_note="定性结果，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "EBV_DNA", "EB病毒DNA", "POSITIVE",
             effective_from=effective_from, source_note="检出即阳性，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "EBV_DNA", "EB病毒DNA", "NEGATIVE",
             effective_from=effective_from, source_note="未检出为阴性"),
        _row(hospital_id, "URINALYSIS", "尿常规", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
        _row(hospital_id, "URINALYSIS", "尿常规", "POSITIVE",
             effective_from=effective_from, source_note="定性结果，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "URINALYSIS", "尿常规", "NEGATIVE",
             effective_from=effective_from, source_note="定性结果，需 LIS 提供 qualitative 字段"),
        _row(hospital_id, "BLOOD_GAS", "血气分析", "ABNORMAL",
             effective_from=effective_from, source_note=panel_note),
    ]
    return rows


HOSPITALS = {
    "FZ_HOSPITAL": _hospital_rows(
        "FZ_HOSPITAL",
        effective_from="2024-06-01",
        ctn_uln=0.05,
        myo_uln=85.0,
        myo_low=3.0,
        ckmb_uln=6.0,
    ),
    "XM_FIRST": _hospital_rows(
        "XM_FIRST",
        effective_from="2024-01-01",
        ctn_uln=0.04,
        myo_uln=77.0,
        myo_low=3.5,
        ckmb_uln=5.0,
    ),
}


def _identity(row: Dict[str, Any]) -> tuple:
    return (
        str(row.get("lab_code") or ""),
        str(row.get("concept_code") or ""),
        row.get("context_gender"),
        row.get("context_age_min"),
        row.get("context_age_max"),
    )


def seed_hospital(hospital_id: str, rows: List[Dict[str, Any]]) -> Dict[str, int]:
    path = STANDARDS_DIR / f"{hospital_id.lower()}.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    seen = {_identity(item) for item in existing}
    added = 0
    for row in rows:
        if _identity(row) in seen:
            continue
        existing.append(row)
        seen.add(_identity(row))
        added += 1
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"hospital_id": hospital_id, "added": added, "total": len(existing)}


def main() -> None:
    STANDARDS_DIR.mkdir(parents=True, exist_ok=True)
    for hospital_id, rows in HOSPITALS.items():
        print(json.dumps(seed_hospital(hospital_id, rows), ensure_ascii=False))


if __name__ == "__main__":
    main()
