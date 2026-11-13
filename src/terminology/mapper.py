"""Terminology mapper - maps hospital local codes to standard codes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from .repository import HospitalTerminologyMappingRepository, MAPPING_PATH


class TerminologyMapper:
    """Maps local hospital codes/labels to standard codes.

    This is the layer that makes the system work across different hospitals
    with different local terminology. Each hospital gets its own mapping table.
    """

    def __init__(self, mapping_path: Optional[str] = None):
        self._lab_map: Dict[str, Dict[str, str]] = {}   # hospital -> {local -> standard}
        self._exam_map: Dict[str, Dict[str, str]] = {}
        self._symptom_map: Dict[str, Dict[str, str]] = {}
        self._disease_map: Dict[str, Dict[str, str]] = {}
        path = Path(mapping_path) if mapping_path else MAPPING_PATH
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Backward-compatible compact format.
                self._lab_map = data.get("labs", {})
                self._exam_map = data.get("exams", {})
                self._symptom_map = data.get("symptoms", {})
                self._disease_map = data.get("diseases", {})
            elif isinstance(data, list):
                repository = HospitalTerminologyMappingRepository(path)
                for item in repository.list(status="approved"):
                    maps = {
                        "lab": self._lab_map,
                        "exam": self._exam_map,
                        "symptom": self._symptom_map,
                        "disease": self._disease_map,
                    }
                    target = maps.get(str(item.get("entity_type") or ""))
                    if target is not None:
                        target.setdefault(str(item.get("hospital_id") or ""), {})[
                            str(item.get("local_code") or "")
                        ] = str(item.get("canonical_code") or "")

    def map_lab_code(self, hospital_id: str, local_code: str) -> str:
        return self._lab_map.get(hospital_id, {}).get(local_code, local_code)

    def map_exam_code(self, hospital_id: str, local_code: str) -> str:
        return self._exam_map.get(hospital_id, {}).get(local_code, local_code)

    def map_symptom_code(self, hospital_id: str, local_code: str) -> str:
        return self._symptom_map.get(hospital_id, {}).get(local_code, local_code)

    def map_disease_code(self, hospital_id: str, local_code: str) -> str:
        return self._disease_map.get(hospital_id, {}).get(local_code, local_code)

    def normalize_patient(self, patient):
        """Normalize all codes in a patient snapshot to standard codes."""
        for lab in patient.labs:
            lab.lab_code = self.map_lab_code(patient.hospital_id, lab.lab_code)
        for exam in patient.exam_findings:
            exam.exam_code = self.map_exam_code(patient.hospital_id, exam.exam_code)
        for symptom in patient.symptoms:
            symptom.symptom_code = self.map_symptom_code(patient.hospital_id, symptom.symptom_code)
        patient.past_history = [
            self.map_disease_code(patient.hospital_id, code)
            for code in patient.past_history
        ]
        return patient

