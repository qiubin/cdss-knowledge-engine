"""Rule repository - loads and manages clinical decision rules."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from ..models.rule import Rule


class RuleRepository(ABC):
    """Abstract interface for rule storage."""

    @abstractmethod
    def get_active_rules(self, department: Optional[str] = None) -> List[Rule]: ...

    @abstractmethod
    def list_all(self) -> List[Rule]:
        """All rules regardless of status, used by staged inference."""

    def get_stage_candidates(
        self,
        rule_types: tuple[str, ...] | list[str],
        department: Optional[str] = None,
        include_draft: bool = False,
    ) -> List[Rule]:
        statuses = {"active", "draft"} if include_draft else {"active"}
        rules = [
            rule for rule in self.list_all()
            if rule.rule_type in set(rule_types) and rule.status in statuses
        ]
        if department:
            rules = [rule for rule in rules if rule.department is None or rule.department == department]
        rules.sort(key=lambda rule: -rule.priority)
        return rules

    @abstractmethod
    def get_treatment_candidates(self, department: Optional[str] = None) -> List[Rule]:
        """Treatment rules stay in draft status and are never auto-enabled.

        They are evaluated only as conditional recommendations when a
        diagnosis rule already matched the same disease.
        """
        ...

    def get_rule(self, rule_id: str) -> Optional[Rule]: ...

    @abstractmethod
    def save_rule(self, rule: Rule) -> None: ...


class JsonRuleRepository(RuleRepository):
    """File-based rule repository. Each file can contain multiple rules."""

    def __init__(self, data_dir: str):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._rules: Dict[str, Rule] = {}
        self._load_all()

    def _load_all(self) -> None:
        managed = self._data_dir / "managed_rules.json"
        files = [managed] if managed.exists() else sorted(self._data_dir.glob("*.json"))
        for f in files:
            items = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(items, list):
                for item in items:
                    rule = Rule(**item)
                    self._rules[rule.rule_id] = rule
            elif isinstance(items, dict):
                rule = Rule(**items)
                self._rules[rule.rule_id] = rule

    def get_active_rules(self, department: Optional[str] = None) -> List[Rule]:
        rules = [r for r in self._rules.values() if r.status == "active"]
        if department:
            rules = [r for r in rules if r.department is None or r.department == department]
        rules.sort(key=lambda r: -r.priority)
        return rules

    def get_treatment_candidates(self, department: Optional[str] = None) -> List[Rule]:
        """Treatment rules stay in draft status and are never auto-enabled.

        They are evaluated only as conditional recommendations when a
        diagnosis rule already matched the same disease.
        """
        rules = [
            r for r in self._rules.values()
            if r.rule_type == "treatment" and r.status in ("draft", "active")
        ]
        if department:
            rules = [r for r in rules if r.department is None or r.department == department]
        rules.sort(key=lambda r: -r.priority)
        return rules

    def get_rule(self, rule_id: str) -> Optional[Rule]:
        return self._rules.get(rule_id)

    def save_rule(self, rule: Rule) -> None:
        self._rules[rule.rule_id] = rule

    def list_all(self) -> List[Rule]:
        return list(self._rules.values())

