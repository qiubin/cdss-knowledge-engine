"""Models for extensible guideline extraction templates."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class TemplateEntityEntry(BaseModel):
    """One canonical clinical entity and its local matching aliases.

    ``standard_code`` maps the stable local code to an external terminology
    (ICD-11 TM1, 中医药学名词, 中国药典, ...).  Local ``code`` never changes
    when the external standard changes.
    """
    code: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    # Discipline ownership: western / tcm / shared. Shared entries (or empty)
    # are visible to every template; a domain-specific entry is only merged
    # into templates of the same domain.
    domain: str = ""
    # External terminology mapping. Western diseases use ICD-10 as the
    # standard code; TCM diseases keep a local stable code plus ICD-11 TM1.
    standard_system: str = ""
    standard_code: str = ""
    source: str = ""
    # Default attributes carried by a dictionary entry (e.g. HERB_ROLE /
    # HERB_DOSE).  Extraction-time values override these defaults.
    attributes: Dict[str, Any] = Field(default_factory=dict)


class TemplateEntityType(BaseModel):
    """A node family such as disease, lab, concept or symptom."""
    entity_type: str
    label: str
    code_field: str = "code"
    concept_required: bool = False
    entries: List[TemplateEntityEntry] = Field(default_factory=list)
    # When True the extractor discovers entities dynamically from guideline
    # text (e.g. disease names) instead of only matching static entries.
    discovery: bool = False
    # When True the type also loads its concrete entities from the shared
    # dictionary at data/vocab/entities/{entity_type}.json. Template-local
    # entries override shared entries with the same code, so a template can
    # both reuse the common terminology and keep domain-specific overrides.
    vocabulary: bool = False
    # Suffixes used by dynamic discovery (e.g. western disease names vs TCM
    # syndrome names ending in 证/证候/型).  Empty keeps the historical
    # western disease suffix list.
    discovery_suffixes: List[str] = Field(default_factory=list)
    # Optional display/UI metadata consumed by entity dictionary pages.
    domain: str = ""
    color: str = ""
    icon: str = ""


class TemplateRelation(BaseModel):
    """A guided relation between template entity types or entity codes."""
    relation_code: str
    name: str
    source_entity_type: str
    target_entity_type: str
    source_code: str = ""
    target_code: str = ""
    cardinality: str = "N:M"
    extraction_hint: str = ""
    concept_code: str = ""
    generate_evidence: bool = True


class TemplateAttribute(BaseModel):
    """An attribute contract attached to one owner entity type."""
    attribute_code: str
    name: str
    owner_entity_type: str
    data_type: str = "string"
    required: bool = False
    allowed_values: List[str] = Field(default_factory=list)
    concept_code: str = ""
    description: str = ""


class ExtractionTemplate(BaseModel):
    """The contract used by both local and LLM extraction providers."""
    template_id: str
    name: str
    description: str = ""
    version: int = 1
    is_default: bool = False
    status: str = "active"
    # Discipline (western / tcm) drives the domain profile.  Human-readable
    # specialty (心血管内科, 血液内科, ...) stays in ``specialty``.
    domain: str = "western"
    specialty: str = ""
    graph_filename: str = ""
    entity_types: List[TemplateEntityType] = Field(default_factory=list)
    relations: List[TemplateRelation] = Field(default_factory=list)
    attributes: List[TemplateAttribute] = Field(default_factory=list)
    concept_codes: List[str] = Field(default_factory=list)
    relation_hints: List[str] = Field(default_factory=list)
    rule_hints: List[str] = Field(default_factory=list)
    prompt_instructions: List[str] = Field(default_factory=list)
    output_contract: Dict[str, Any] = Field(default_factory=dict)
    # Declares which extraction workflows this template supports, e.g.
    # "diagnosis" or "treatment". Downstream generation reads the same flags
    # to decide whether treatment rules may be produced from a template.
    capabilities: List[str] = Field(default_factory=lambda: ["diagnosis"])
    # Template-driven rule generation contract.  Each entry names a rule type
    # and the entities/relations that feed it, so the generator stays generic
    # while domain-specific parameters live in data.
    rule_generation: List[Dict[str, Any]] = Field(default_factory=list)
    # External terminology the template's codes are mapped to (optional).
    standard_system: str = ""

    @property
    def allowed_entity_types(self) -> List[str]:
        return [item.entity_type for item in self.entity_types]

    @property
    def known_codes(self) -> set[str]:
        values = set(self.concept_codes)
        for entity_type in self.entity_types:
            values.update(item.code for item in entity_type.entries)
        return values

    def summary(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "is_default": self.is_default,
            "status": self.status,
            "domain": self.domain,
            "specialty": self.specialty,
            "entity_type_count": len(self.entity_types),
            "entity_entry_count": sum(len(item.entries) for item in self.entity_types),
            "relation_count": len(self.relations),
            "attribute_count": len(self.attributes),
            "concept_count": len(self.concept_codes),
            "hint_count": len(self.relation_hints) + len(self.rule_hints) + len(self.prompt_instructions),
            "capabilities": list(self.capabilities),
            "rule_generation_count": len(self.rule_generation),
            "standard_system": self.standard_system,
        }

