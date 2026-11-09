"""Seed switchable demo patient cases for the inference test page."""
from __future__ import annotations

import json
from pathlib import Path

PATIENTS = Path("data/patients")
PATIENTS.mkdir(parents=True, exist_ok=True)


def lab(code, name, value, unit, low=None, high=None, collected="", reported=""):
    return {
        "lab_code": code,
        "lab_name": name,
        "value": value,
        "unit": unit,
        "reference_low": low,
        "reference_high": high,
        "collected_at": collected,
        "reported_at": reported,
    }


def symptom(code, name, present=True, duration=None, severity=None):
    return {
        "symptom_code": code,
        "symptom_name": name,
        "present": present,
        "duration_minutes": duration,
        "severity": severity,
    }


def finding(exam_code, exam_name, finding_code, finding_name, present=True, summary=""):
    return {
        "exam_code": exam_code,
        "exam_name": exam_name,
        "finding_code": finding_code,
        "finding_name": finding_name,
        "present": present,
        "detail": {"summary": summary} if summary else None,
    }


def vital(code, value, unit):
    return {"vital_code": code, "value": value, "unit": unit}


CASES = [
    {
        "case_id": "stemi-anterior",
        "category": "STEMI",
        "file": "demo_stemi_patient.json",
        "name": "STEMI · 急性前壁心肌梗死",
        "domain": "western",
        "description": "58 岁男性，持续压榨性胸痛 40 分钟，V2-V5 导联 ST 段抬高，肌钙蛋白动态升高。",
        "default": True,
    },
    {
        "case_id": "stemi-inferior",
        "category": "STEMI",
        "file": "demo_stemi_inferior_patient.json",
        "name": "STEMI · 急性下壁心肌梗死伴低血压",
        "domain": "western",
        "description": "66 岁女性，胸痛伴恶心呕吐，II/III/aVF 导联 ST 段抬高，血压偏低、心率偏慢。",
        "default": False,
    },
    {
        "case_id": "fulminant-myocarditis-adult",
        "category": "暴发性心肌炎",
        "file": "demo_fulminant_myocarditis_adult.json",
        "name": "暴发性心肌炎 · 成人病毒性",
        "domain": "western",
        "description": "32 岁男性，发热后胸闷气促 3 天，肌钙蛋白显著升高，EF 30%，心源性休克。",
        "default": False,
    },
    {
        "case_id": "fulminant-myocarditis-youth",
        "category": "暴发性心肌炎",
        "file": "demo_fulminant_myocarditis_youth.json",
        "name": "暴发性心肌炎 · 青少年",
        "domain": "western",
        "description": "16 岁女性，腹痛呕吐后晕厥，广泛 ST-T 改变、频发室早，血压 78/45 mmHg。",
        "default": False,
    },
    {
        "case_id": "myocarditis-subacute",
        "category": "急性心肌炎",
        "file": "demo_myocarditis_subacute.json",
        "name": "急性心肌炎 · 亚急性轻型",
        "domain": "western",
        "description": "45 岁男性，上呼吸道感染后 5 天胸闷心悸，肌钙蛋白轻度升高，EF 48%。",
        "default": False,
    },
]


def stemi_anterior() -> dict:
    path = PATIENTS / "demo_stemi_patient.json"
    patient = json.loads(path.read_text(encoding="utf-8"))
    patient.update({
        "display_name": "STEMI · 急性前壁心肌梗死",
        "category": "STEMI",
        "chief_complaint": "持续压榨性胸痛 40 分钟，伴大汗。",
        "history_present": "患者晨起活动后出现胸骨后压榨性疼痛，休息后未缓解，伴大汗、恶心，急诊送入医院。",
        "history_past": "高血压 5 年，2 型糖尿病 3 年。",
    })
    return patient


def stemi_inferior() -> dict:
    return {
        "patient_id": "DEMO-STEMI-INFERIOR",
        "hospital_id": "XM_FIRST",
        "age": 66,
        "gender": "F",
        "encounter_time": "2026-11-04T06:40:00+08:00",
        "display_name": "STEMI · 急性下壁心肌梗死伴低血压",
        "category": "STEMI",
        "chief_complaint": "胸痛伴恶心呕吐 1.5 小时，头晕。",
        "history_present": "患者凌晨突发胸骨后压榨性疼痛，放射至后背，伴恶心、呕吐、大汗，起身时头晕黑朦。",
        "history_past": "高血压 10 年，高脂血症 5 年。",
        "labs": [
            lab("MYO", "肌红蛋白", 210.0, "ng/mL", 3.5, 77.0, "2026-11-04T06:45:00+08:00", "2026-11-04T06:55:00+08:00"),
            lab("CTN", "肌钙蛋白", 2.8, "ng/mL", 0.0, 0.04, "2026-11-04T06:45:00+08:00", "2026-11-04T07:00:00+08:00"),
            lab("CTN", "肌钙蛋白", 6.5, "ng/mL", 0.0, 0.04, "2026-11-04T09:00:00+08:00", "2026-11-04T09:15:00+08:00"),
            lab("CK_MB", "肌酸激酶同工酶", 45.0, "ng/mL", 0.0, 5.0, "2026-11-04T06:45:00+08:00", "2026-11-04T07:00:00+08:00"),
            lab("BNP", "脑钠肽", 350.0, "pg/mL", 0.0, 100.0, "2026-11-04T06:45:00+08:00", "2026-11-04T07:10:00+08:00"),
        ],
        "exam_findings": [
            finding("ECG_12LEAD", "12导联心电图", "ST_CONVEX_ELEVATION", "ST段弓背向上抬高", True, "II、III、aVF 导联抬高 0.25mV，I、aVL 导联镜像压低"),
            finding("ECG_12LEAD", "12导联心电图", "PATHOLOGICAL_Q_WAVE", "病理性Q波", False),
            finding("ECHO", "超声心动图", "LEFT_VENTRICULAR_DYSFUNCTION", "左室下壁运动减弱", True, "下壁及后壁节段性运动减弱，EF 45%"),
        ],
        "symptoms": [
            symptom("CHEST_PAIN_CRUSHING", "压榨性胸痛", True, 90, "severe"),
            symptom("NAUSEA_VOMITING", "恶心呕吐", True, 60, "moderate"),
            symptom("DIAPHORESIS", "大汗", True),
            symptom("DIZZINESS", "头晕", True),
        ],
        "vitals": [
            vital("sbp", 88.0, "mmHg"),
            vital("dbp", 55.0, "mmHg"),
            vital("hr", 52.0, "bpm"),
            vital("rr", 18.0, "bpm"),
            vital("spo2", 96.0, "%"),
        ],
        "past_history": ["I10", "E78.5"],
        "current_medications": ["ASPIRIN", "ATORVASTATIN"],
        "allergies": [],
    }


def fulminant_adult() -> dict:
    return {
        "patient_id": "DEMO-FULMINANT-MYO-ADULT",
        "hospital_id": "XM_FIRST",
        "age": 32,
        "gender": "M",
        "encounter_time": "2026-11-05T21:10:00+08:00",
        "display_name": "暴发性心肌炎 · 成人病毒性",
        "category": "暴发性心肌炎",
        "chief_complaint": "发热 3 天，胸闷、气促 1 天，不能平卧。",
        "history_present": "患者 3 天前出现发热、咽痛、全身酸痛，1 天前出现胸闷、气短，夜间不能平卧，尿量减少，急诊测血压偏低。",
        "history_past": "既往体健，无冠心病、高血压、糖尿病史。",
        "labs": [
            lab("CTN", "肌钙蛋白", 3.2, "ng/mL", 0.0, 0.04, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("CTN", "肌钙蛋白", 7.8, "ng/mL", 0.0, 0.04, "2026-11-06T01:00:00+08:00", "2026-11-06T01:20:00+08:00"),
            lab("CK_MB", "肌酸激酶同工酶", 60.0, "ng/mL", 0.0, 5.0, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("MYO", "肌红蛋白", 180.0, "ng/mL", 3.5, 77.0, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("BNP", "脑钠肽", 1800.0, "pg/mL", 0.0, 100.0, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("CRP", "C反应蛋白", 85.0, "mg/L", 0.0, 10.0, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("LDH", "乳酸脱氢酶", 620.0, "U/L", 120.0, 250.0, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("CREAT", "肌酐", 150.0, "umol/L", 57.0, 111.0, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
            lab("LACTATE", "乳酸", 4.2, "mmol/L", 0.5, 2.2, "2026-11-05T21:15:00+08:00", "2026-11-05T21:30:00+08:00"),
        ],
        "exam_findings": [
            finding("ECG_12LEAD", "12导联心电图", "ST_CONVEX_ELEVATION", "广泛ST段抬高", True, "I、II、aVL、V2-V6 导联广泛 ST 段抬高，伴 T 波高尖"),
            finding("ECG_12LEAD", "12导联心电图", "PATHOLOGICAL_Q_WAVE", "病理性Q波", False),
            finding("ECHO", "超声心动图", "LEFT_VENTRICULAR_DYSFUNCTION", "左室弥漫性收缩功能减低", True, "左室弥漫性运动减弱，EF 30%，少量心包积液"),
            finding("CXR", "胸部X线", "LUNG_RALES", "肺淤血", True, "双肺纹理增粗，肺门影增大"),
        ],
        "symptoms": [
            symptom("FEVER", "发热", True, 4320, "severe"),
            symptom("SYM_CHEST_TIGHTNESS", "胸闷", True, 1440, "severe"),
            symptom("SYM_SHORTNESS_BREATH", "气短", True, 1440, "severe"),
            symptom("SYM_PALPITATION", "心悸", True, 720, "moderate"),
            symptom("SYM_FATIGUE", "乏力", True, 4320, "severe"),
        ],
        "vitals": [
            vital("sbp", 82.0, "mmHg"),
            vital("dbp", 50.0, "mmHg"),
            vital("hr", 124.0, "bpm"),
            vital("rr", 28.0, "bpm"),
            vital("spo2", 90.0, "%"),
            vital("temp", 38.2, "℃"),
        ],
        "past_history": [],
        "current_medications": [],
        "allergies": [],
    }


def fulminant_youth() -> dict:
    return {
        "patient_id": "DEMO-FULMINANT-MYO-YOUTH",
        "hospital_id": "XM_FIRST",
        "age": 16,
        "gender": "F",
        "encounter_time": "2026-11-06T03:20:00+08:00",
        "display_name": "暴发性心肌炎 · 青少年",
        "category": "暴发性心肌炎",
        "chief_complaint": "腹痛、呕吐 2 天，晕厥 1 次。",
        "history_present": "患者 2 天前出现腹痛、呕吐、乏力，按胃肠炎治疗无好转；今晨突发晕厥，意识恢复后仍面色苍白、四肢湿冷。",
        "history_past": "既往体健，近期有上呼吸道感染史。",
        "labs": [
            lab("CTN", "肌钙蛋白", 5.6, "ng/mL", 0.0, 0.04, "2026-11-06T03:25:00+08:00", "2026-11-06T03:40:00+08:00"),
            lab("CK_MB", "肌酸激酶同工酶", 80.0, "ng/mL", 0.0, 5.0, "2026-11-06T03:25:00+08:00", "2026-11-06T03:40:00+08:00"),
            lab("MYO", "肌红蛋白", 260.0, "ng/mL", 3.5, 77.0, "2026-11-06T03:25:00+08:00", "2026-11-06T03:40:00+08:00"),
            lab("BNP", "脑钠肽", 2200.0, "pg/mL", 0.0, 100.0, "2026-11-06T03:25:00+08:00", "2026-11-06T03:40:00+08:00"),
            lab("CRP", "C反应蛋白", 60.0, "mg/L", 0.0, 10.0, "2026-11-06T03:25:00+08:00", "2026-11-06T03:40:00+08:00"),
            lab("LACTATE", "乳酸", 5.1, "mmol/L", 0.5, 2.2, "2026-11-06T03:25:00+08:00", "2026-11-06T03:40:00+08:00"),
        ],
        "exam_findings": [
            finding("ECG_12LEAD", "12导联心电图", "ST_CONVEX_ELEVATION", "广泛ST段抬高", True, "V2-V6、I、aVL 导联 ST 段抬高，频发室性早搏"),
            finding("ECG_12LEAD", "12导联心电图", "PATHOLOGICAL_Q_WAVE", "病理性Q波", False),
            finding("ECHO", "超声心动图", "LEFT_VENTRICULAR_DYSFUNCTION", "左室弥漫性收缩功能减低", True, "EF 25%，心包少量积液"),
        ],
        "symptoms": [
            symptom("ABDOMINAL_PAIN", "腹痛", True, 2880, "moderate"),
            symptom("VOMITING", "呕吐", True, 2880, "moderate"),
            symptom("SYNCOPE", "晕厥", True, 5, "severe"),
            symptom("SYM_FATIGUE", "乏力", True, 2880, "severe"),
            symptom("SYM_SHORTNESS_BREATH", "气短", True, 720, "severe"),
        ],
        "vitals": [
            vital("sbp", 78.0, "mmHg"),
            vital("dbp", 45.0, "mmHg"),
            vital("hr", 140.0, "bpm"),
            vital("rr", 30.0, "bpm"),
            vital("spo2", 88.0, "%"),
            vital("temp", 37.8, "℃"),
        ],
        "past_history": [],
        "current_medications": [],
        "allergies": [],
    }


def subacute_myocarditis() -> dict:
    return {
        "patient_id": "DEMO-MYO-SUBACUTE",
        "hospital_id": "XM_FIRST",
        "age": 45,
        "gender": "M",
        "encounter_time": "2026-11-06T10:00:00+08:00",
        "display_name": "急性心肌炎 · 亚急性轻型",
        "category": "急性心肌炎",
        "chief_complaint": "上呼吸道感染后胸闷、心悸 5 天。",
        "history_present": "患者 5 天前上呼吸道感染后出现活动后胸闷、心悸，休息可缓解，无晕厥，无胸痛放射。",
        "history_past": "既往体健。",
        "labs": [
            lab("CTN", "肌钙蛋白", 0.8, "ng/mL", 0.0, 0.04, "2026-11-06T10:05:00+08:00", "2026-11-06T10:20:00+08:00"),
            lab("CK_MB", "肌酸激酶同工酶", 18.0, "ng/mL", 0.0, 5.0, "2026-11-06T10:05:00+08:00", "2026-11-06T10:20:00+08:00"),
            lab("CRP", "C反应蛋白", 30.0, "mg/L", 0.0, 10.0, "2026-11-06T10:05:00+08:00", "2026-11-06T10:20:00+08:00"),
            lab("BNP", "脑钠肽", 260.0, "pg/mL", 0.0, 100.0, "2026-11-06T10:05:00+08:00", "2026-11-06T10:20:00+08:00"),
        ],
        "exam_findings": [
            finding("ECG_12LEAD", "12导联心电图", "ST_CONVEX_ELEVATION", "ST段轻度抬高", True, "V4-V6 导联 ST 段轻度抬高 0.1mV"),
            finding("ECHO", "超声心动图", "LEFT_VENTRICULAR_DYSFUNCTION", "左室收缩功能轻度减低", True, "EF 48%，节段性运动稍减弱"),
        ],
        "symptoms": [
            symptom("SYM_CHEST_TIGHTNESS", "胸闷", True, 720, "moderate"),
            symptom("SYM_PALPITATION", "心悸", True, 720, "mild"),
            symptom("SYM_FATIGUE", "乏力", True, 2880, "mild"),
        ],
        "vitals": [
            vital("sbp", 118.0, "mmHg"),
            vital("dbp", 72.0, "mmHg"),
            vital("hr", 92.0, "bpm"),
            vital("rr", 18.0, "bpm"),
            vital("spo2", 98.0, "%"),
            vital("temp", 37.2, "℃"),
        ],
        "past_history": [],
        "current_medications": [],
        "allergies": [],
    }


def main() -> None:
    (PATIENTS / "demo_stemi_patient.json").write_text(
        json.dumps(stemi_anterior(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PATIENTS / "demo_stemi_inferior_patient.json").write_text(
        json.dumps(stemi_inferior(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PATIENTS / "demo_fulminant_myocarditis_adult.json").write_text(
        json.dumps(fulminant_adult(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PATIENTS / "demo_fulminant_myocarditis_youth.json").write_text(
        json.dumps(fulminant_youth(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PATIENTS / "demo_myocarditis_subacute.json").write_text(
        json.dumps(subacute_myocarditis(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PATIENTS / "cases.json").write_text(
        json.dumps({"cases": CASES}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("seeded", len(CASES), "demo cases")


if __name__ == "__main__":
    main()
