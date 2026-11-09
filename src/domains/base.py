"""Domain profiles: the extension point that separates shared CDSS plumbing
from discipline-specific graph semantics, rule generation and UI.

The shared layer (PDF parsing, templates, entity dictionaries, concept
vocabulary, knowledge packages, provenance) must not assume a western-medicine
diagnosis model.  Everything that *does* assume it lives here so a new
discipline can be added as a profile instead of a second copy of the platform.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


WESTERN = "western"
TCM = "tcm"


@dataclass(frozen=True)
class GraphProfile:
    """Static contract for one discipline's knowledge graph."""

    domain: str
    label: str
    # Node types that act as the graph's "center" (rules, package targets,
    # Neo4j focus).  Western medicine anchors on disease; TCM anchors on
    # syndrome (with disease as a secondary anchor).
    anchor_types: Tuple[str, ...]
    # Entity types accepted from extraction templates and graph imports.
    allowed_entity_types: Tuple[str, ...]
    # Entity types projected into unified diagnostic-evidence rows.  TCM leaves
    # this empty: syndrome conditions reference observation nodes directly.
    evidence_entity_types: Tuple[str, ...] = ()
    # Historical default when a rule carries no graph_filename.
    legacy_graph_filename: str = "stemi_graph.json"


@dataclass(frozen=True)
class InferenceStage:
    """One ordered inference stage.

    Stages run in declaration order.  ``rule_types`` selects the rules that
    belong to the stage; ``catch_all`` makes a stage absorb every rule type
    not claimed by a later stage (keeps historical western behaviour).
    """

    name: str
    rule_types: Tuple[str, ...]
    description: str = ""
    catch_all: bool = False
    draft_allowed: bool = False
    produces_facts: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainProfile:
    domain: str
    label: str
    description: str
    default_template_id: str
    graph: GraphProfile
    stages: Tuple[InferenceStage, ...]
    # Frontend contract: node_type -> {label, color, stroke, shape, size}
    node_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Frontend contract: panels describing the domain's reasoning paths.
    path_panels: Tuple[Dict[str, Any], ...] = ()
    # Frontend contract: fields shown on the patient/inference form.
    inference_form: Tuple[Dict[str, Any], ...] = ()
    # Knowledge-package checks specific to this domain (common checks always
    # run first).
    package_checks: Tuple[str, ...] = ()

    def anchor_types(self) -> Tuple[str, ...]:
        return self.graph.anchor_types

    def stage_for_rule_type(self, rule_type: str) -> Optional[InferenceStage]:
        explicit = [
            stage for stage in self.stages
            if rule_type in stage.rule_types
        ]
        if explicit:
            return explicit[0]
        for stage in self.stages:
            if stage.catch_all:
                return stage
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "label": self.label,
            "description": self.description,
            "default_template_id": self.default_template_id,
            "anchor_types": list(self.graph.anchor_types),
            "allowed_entity_types": list(self.graph.allowed_entity_types),
            "stages": [
                {
                    "name": stage.name,
                    "rule_types": list(stage.rule_types),
                    "description": stage.description,
                    "draft_allowed": stage.draft_allowed,
                    "produces_facts": list(stage.produces_facts),
                }
                for stage in self.stages
            ],
            "node_meta": self.node_meta,
            "path_panels": list(self.path_panels),
            "inference_form": list(self.inference_form),
            "package_checks": list(self.package_checks),
        }


class RuleGenerator:
    """Interface for template-driven candidate rule generation."""

    rule_type: str = ""

    def generate(
        self,
        entities: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
        template: Any,
        doc_id: str,
        doc_title: str = "",
        relations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError
