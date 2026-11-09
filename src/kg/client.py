"""Knowledge graph client - abstract interface + JSON file implementation."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class DiagnosticEvidence:
    """A KG node bridging concepts to clinical meaning."""

    def __init__(self, data: Dict[str, Any]):
        self.id: str = data["id"]
        self.description: str = data.get("description", "")
        self.data_type: str = data.get("data_type", "")
        self.lab_code: Optional[str] = data.get("lab_code")
        self.concept_code: Optional[str] = data.get("concept_code")
        self.exam_code: Optional[str] = data.get("exam_code")
        self.finding_code: Optional[str] = data.get("finding_code")
        self.symptom_code: Optional[str] = data.get("symptom_code")
        self.parameter: Optional[Dict[str, Any]] = data.get("parameter")
        self.source_refs: List[str] = data.get("source_refs", [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "description": self.description,
            "data_type": self.data_type, "lab_code": self.lab_code,
            "concept_code": self.concept_code, "exam_code": self.exam_code,
            "finding_code": self.finding_code, "symptom_code": self.symptom_code,
            "parameter": self.parameter, "source_refs": self.source_refs,
        }


class Source:
    """A citation to a guideline document location."""

    def __init__(self, data: Dict[str, Any]):
        self.chunk_id: str = data["chunk_id"]
        self.doc_id: str = data.get("doc_id", "")
        self.doc_title: str = data.get("doc_title", "")
        self.section: str = data.get("section", "")
        self.page_start: int = data.get("page_start", 0)
        self.page_end: int = data.get("page_end", 0)
        self.line_start: int = data.get("line_start", 0)
        self.line_end: int = data.get("line_end", 0)
        self.quote: str = data.get("quote", "")


class Disease:
    """A disease entity in the graph."""

    def __init__(self, data: Dict[str, Any]):
        self.code: str = data["code"]
        self.name_cn: str = data.get("name_cn", "")
        self.name_en: str = data.get("name_en", "")


class KnowledgeGraphClient(ABC):
    """Abstract interface for the knowledge graph."""

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> Optional[DiagnosticEvidence]: ...

    @abstractmethod
    def get_source(self, chunk_id: str) -> Optional[Source]: ...

    @abstractmethod
    def get_disease(self, code: str) -> Optional[Disease]: ...

    @abstractmethod
    def get_evidence_for_disease(self, disease_code: str) -> List[Dict[str, Any]]: ...

    def get_node_name(self, node_type: str, code: str) -> str:
        """Display name for any semantic node type.

        Western graphs only expose diseases to the inference engine; TCM adds
        syndrome lookups.  Implementations may override this to resolve names
        from the unified ``nodes`` layer.
        """
        if node_type == "disease":
            disease = self.get_disease(code)
            return disease.name_cn if disease else ""
        return ""


class JsonGraphClient(KnowledgeGraphClient):
    """File-based knowledge graph for development and testing."""

    def __init__(self, graph_path: str):
        self._data: Dict[str, Any] = json.loads(
            Path(graph_path).read_text(encoding="utf-8")
        )
        self._evidence_index: Dict[str, DiagnosticEvidence] = {}
        for ev_data in self._data.get("evidences", []):
            ev = DiagnosticEvidence(ev_data)
            self._evidence_index[ev.id] = ev

        self._source_index: Dict[str, Source] = {}
        for src_data in self._data.get("sources", []):
            src = Source(src_data)
            self._source_index[src.chunk_id] = src

        self._disease_index: Dict[str, Disease] = {}
        for dis_data in self._data.get("diseases", []):
            dis = Disease(dis_data)
            self._disease_index[dis.code] = dis

        self._disease_evidence: Dict[str, List[Dict[str, Any]]] = {}
        for rel in self._data.get("disease_evidence", []):
            code = rel["disease_code"]
            self._disease_evidence.setdefault(code, []).append(rel)

        raw_nodes = self._data.get("nodes") or []
        if isinstance(raw_nodes, dict):
            raw_nodes = list(raw_nodes.values())
        self._node_index: Dict[tuple, Dict[str, Any]] = {}
        for node in raw_nodes:
            if not isinstance(node, dict) or not node.get("node_type"):
                continue
            code = node.get("code") or node.get("concept_code")
            if code:
                self._node_index[(str(node["node_type"]), str(code))] = node

    def get_evidence(self, evidence_id: str) -> Optional[DiagnosticEvidence]:
        return self._evidence_index.get(evidence_id)

    def get_source(self, chunk_id: str) -> Optional[Source]:
        return self._source_index.get(chunk_id)

    def get_disease(self, code: str) -> Optional[Disease]:
        return self._disease_index.get(code)

    def get_evidence_for_disease(self, disease_code: str) -> List[Dict[str, Any]]:
        return self._disease_evidence.get(disease_code, [])

    def get_node_name(self, node_type: str, code: str) -> str:
        node = self._node_index.get((str(node_type), str(code)))
        if node and node.get("name"):
            return str(node["name"])
        if node_type == "disease":
            disease = self.get_disease(code)
            return disease.name_cn if disease else ""
        return ""

    @property
    def raw_data(self) -> Dict[str, Any]:
        return self._data

