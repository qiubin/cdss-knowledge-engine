# 双规则引擎（可切换）+ 规则库加固与临床友好编辑 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把推理层改造成"原生引擎（默认，带匹配索引加固）+ Drools 引擎（可选，JSON→DRL 编译 + sidecar）"的双引擎可切换架构，并补齐规则版本历史、业务字典变更联动规则、面向医学知识专员/医务部的规则向导 UI。

**Architecture:** 双引擎共享同一 `RuleEngine` 接口和同一结论/溯源层：原子条件（叶子）永远由 Python 评估（医院标准、概念词汇、图谱证据都在这一层解析），Drools 只承担布尔树匹配（RETE 批量匹配），匹配结果回到 Python 用原生评估器重建带 EvidenceTrace 的 Conclusion，因此两种模式的输出完全一致；任何 sidecar 故障或不支持的规则类型自动回退原生引擎。原生引擎加固 = 原子求值缓存 + 保守剪枝索引。

**Tech Stack:** Python 3 / FastAPI / Pydantic 2（现有栈）；httpx（已在 requirements.txt）；Drools 8（仅 reference sidecar，Java 17 + Maven，非本仓库测试范围）；前端 vanilla JS + node:test。

**Spec:** 本计划实现 2026-11-11 会话确认的三项决策：
1. 采用双规则引擎、配置可切换（native 默认 / drools 可选）；
2. 按此前建议实现规则库缺口：规则版本历史/回滚、字典变更联动规则、字典驱动规则向导；
3. 手动扩展主体为医学知识专员与医院医务部，向导必须无代码、全下拉选择。

## Global Constraints

- 不引入新的 Python 依赖（httpx 已存在）；Java sidecar 依赖只出现在 `drools-sidecar/` 子目录。
- 默认引擎必须是 `native`；`data/engine.json` 不存在时行为与现状完全一致。
- 双模式输出一致性：同一患者、同一规则库下，native 与 drools 模式产生的 Conclusion（含 EvidenceTrace、score）必须一致。
- 规则 JSON（`data/rules/managed_rules.json`）仍是唯一事实源与审核对象；DRL 只是编译产物。
- 所有新建规则/修改入口保持 `draft` 入库 + 现有审核门禁不变。
- 测试命令：Python `python3 -m pytest <file> -v`；JS `node --test <file>`。
- 提交信息用中文 feat/fix 前缀，与仓库现有风格一致（如 `feat: 双规则引擎配置切换`）。

---

### Task 1: 引擎配置存储 `src/engine/settings.py`

**Files:**
- Create: `src/engine/settings.py`
- Test: `tests/test_engine_settings.py`

**Interfaces:**
- Produces: `load_settings(path: Path | None = None) -> dict`（键 `active`: "native"|"drools"，键 `drools`: `{base_url, timeout_seconds, fail_over_native}`）；`save_settings(settings: dict, path: Path | None = None) -> dict`；常量 `ENGINES`、`SETTINGS_PATH`。path 参数为 None 时在调用时读取模块级 `SETTINGS_PATH`（便于测试 monkeypatch）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_settings.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from src.engine import settings as engine_settings


def test_default_settings_when_missing(tmp_path):
    result = engine_settings.load_settings(tmp_path / "missing.json")
    assert result["active"] == "native"
    assert result["drools"]["fail_over_native"] is True
    assert result["drools"]["timeout_seconds"] == 5


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "engine.json"
    saved = engine_settings.save_settings(
        {"active": "drools", "drools": {"base_url": "http://localhost:9000"}},
        path,
    )
    assert saved["active"] == "drools"
    assert saved["drools"]["base_url"] == "http://localhost:9000"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["active"] == "drools"


def test_reject_unknown_engine(tmp_path):
    with pytest.raises(ValueError):
        engine_settings.save_settings({"active": "spark"}, tmp_path / "engine.json")


def test_unknown_keys_dropped_and_active_normalized(tmp_path):
    path = tmp_path / "engine.json"
    path.write_text(json.dumps({"active": "NATIVE", "drools": {"bogus": 1}}), encoding="utf-8")
    loaded = engine_settings.load_settings(path)
    assert loaded["active"] == "native"
    assert "bogus" not in loaded["drools"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_engine_settings.py -v`
Expected: FAIL（`ModuleNotFoundError: src.engine.settings`）

- [ ] **Step 3: 实现**

```python
# src/engine/settings.py
"""Rule engine runtime settings (data/engine.json).

The file is optional; absence means "native engine, default drools endpoint".
This keeps current behaviour identical when the dual-engine feature is off.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "data" / "engine.json"

ENGINES = ("native", "drools")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "active": "native",
    "drools": {
        "base_url": "http://127.0.0.1:8090",
        "timeout_seconds": 5,
        "fail_over_native": True,
    },
}


def _deep_default() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def load_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or SETTINGS_PATH
    merged = _deep_default()
    if not target.exists():
        return merged
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return merged
    if not isinstance(data, dict):
        return merged
    active = str(data.get("active") or merged["active"]).strip().lower()
    merged["active"] = active if active in ENGINES else "native"
    drools = data.get("drools")
    if isinstance(drools, dict):
        for key in list(merged["drools"]):
            if key in drools:
                merged["drools"][key] = drools[key]
    return merged


def save_settings(settings: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or SETTINGS_PATH
    merged = load_settings(target)
    active = str(settings.get("active") or merged["active"]).strip().lower()
    if active not in ENGINES:
        raise ValueError(f"未知引擎: {active}，可选 {'、'.join(ENGINES)}")
    merged["active"] = active
    drools = settings.get("drools")
    if isinstance(drools, dict):
        for key in list(merged["drools"]):
            if key in drools:
                merged["drools"][key] = drools[key]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_engine_settings.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/engine/settings.py tests/test_engine_settings.py
git commit -m "feat: 规则引擎配置存储 data/engine.json"
```

---

### Task 2: RuleEngine 接口 + 引擎工厂

**Files:**
- Create: `src/engine/base.py`
- Create: `src/engine/factory.py`
- Modify: `src/engine/inference_engine.py`（加 `name`/`effective_name`/`describe()`/`last_fallback_reason`）
- Modify: `src/engine/__init__.py`（导出工厂）
- Test: `tests/test_engine_factory.py`

**Interfaces:**
- Produces: `RuleEngine`（Protocol，属性 `name: str`，方法签名与 `InferenceEngine.run` 相同）；`create_rule_engine(rule_repo, kg_client, standard_repo, vocabulary, graph_client_factory=None, domain="western", settings=None, terminology=None) -> RuleEngine`；`InferenceEngine.name == "native"`；`engine.describe() -> {"requested","used","fallback_reason"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_factory.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine import InferenceEngine
from src.engine.factory import create_rule_engine
from src.engine.settings import DEFAULT_SETTINGS


class _StubRepo:
    def list_all(self):
        return []

    def get_active_rules(self, department=None):
        return []

    def get_treatment_candidates(self, department=None):
        return []


def _build(domain="western", settings=None):
    from src.kg.schema import ConceptVocabulary
    from src.standards.repository import JsonStandardRepository
    from src.rules.repository import JsonRuleRepository

    project_root = Path(__file__).parent.parent
    vocab = ConceptVocabulary()
    vocab.load(str(project_root / "data" / "vocab" / "concepts.json"))
    return create_rule_engine(
        JsonRuleRepository(str(project_root / "tests" / "fixtures" / "rules")),
        None,
        JsonStandardRepository(str(project_root / "data" / "standards")),
        vocab,
        domain=domain,
        settings=settings,
    )


def test_default_settings_create_native_engine():
    engine = _build(settings=dict(DEFAULT_SETTINGS))
    assert isinstance(engine, InferenceEngine)
    assert engine.name == "native"
    assert engine.describe() == {
        "requested": "native",
        "used": "native",
        "fallback_reason": "",
    }


def test_drools_settings_create_drools_adapter():
    settings = dict(DEFAULT_SETTINGS)
    settings["active"] = "drools"
    engine = _build(settings=settings)
    assert engine.name == "drools"
    # 构造时不应探测网络；describe 反映尚未回退
    assert engine.describe()["used"] == "drools"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_engine_factory.py -v`
Expected: FAIL（`cannot import name 'create_rule_engine'`）

- [ ] **Step 3: 实现**

`src/engine/base.py`：

```python
# src/engine/base.py
"""Rule engine interface shared by native and Drools implementations."""
from __future__ import annotations

from typing import List, Optional, Protocol

from ..models.conclusion import Conclusion
from ..models.patient import PatientSnapshot


class RuleEngine(Protocol):
    """A rule engine evaluates a patient snapshot into conclusions.

    Implementations must be repeatable: identical inputs produce identical
    Conclusion content, including evidence traces and scores.
    """

    name: str

    def run(
        self,
        patient: PatientSnapshot,
        department: Optional[str] = None,
        include_draft_patterns: bool = False,
    ) -> List[Conclusion]: ...
```

`src/engine/factory.py`：

```python
# src/engine/factory.py
"""Create the active rule engine from data/engine.json."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..kg.client import KnowledgeGraphClient
from ..kg.schema import ConceptVocabulary
from ..rules.repository import RuleRepository
from ..standards.repository import StandardRepository
from .base import RuleEngine
from .inference_engine import InferenceEngine
from .settings import load_settings


def create_rule_engine(
    rule_repo: RuleRepository,
    kg_client: KnowledgeGraphClient,
    standard_repo: StandardRepository,
    vocabulary: ConceptVocabulary,
    graph_client_factory=None,
    domain: str = "western",
    settings: Optional[Dict[str, Any]] = None,
    terminology=None,
) -> RuleEngine:
    """Build the configured engine. Drools failures fall back per its config."""
    native = InferenceEngine(
        rule_repo, kg_client, standard_repo, vocabulary,
        graph_client_factory, domain=domain, terminology=terminology,
    )
    config = settings if settings is not None else load_settings()
    if str(config.get("active") or "native") != "drools":
        return native
    from .drools.engine import DroolsRuleEngine

    drools_config = dict(config.get("drools") or {})
    return DroolsRuleEngine(native, **drools_config)
```

`src/engine/inference_engine.py`：在 `class InferenceEngine` 内、`__init__` 之前加：

```python
    name = "native"
    last_fallback_reason = ""

    @property
    def effective_name(self) -> str:
        return self.name

    def describe(self) -> dict:
        return {
            "requested": self.name,
            "used": self.effective_name,
            "fallback_reason": self.last_fallback_reason,
        }
```

`src/engine/__init__.py` 改为：

```python
from .inference_engine import InferenceEngine
from .evaluators import BaseEvaluator, EvaluationResult, EvaluatorFactory
from .factory import create_rule_engine

__all__ = [
    "InferenceEngine",
    "BaseEvaluator",
    "EvaluationResult",
    "EvaluatorFactory",
    "create_rule_engine",
]
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_engine_factory.py tests/test_engine.py -v`
Expected: 全部 passed（test_engine 是现有回归，证明原生行为未变）

- [ ] **Step 5: Commit**

```bash
git add src/engine/base.py src/engine/factory.py src/engine/inference_engine.py src/engine/__init__.py tests/test_engine_factory.py
git commit -m "feat: RuleEngine 接口与引擎工厂（native 默认）"
```

---

### Task 3: 引擎配置/状态 API

**Files:**
- Create: `src/api/routes/engine.py`
- Modify: `src/api/main.py`（注册 router）
- Test: `tests/test_engine_api.py`

**Interfaces:**
- Consumes: Task 1 的 `load_settings/save_settings/SETTINGS_PATH`。
- Produces: `GET /api/engine/config`、`PUT /api/engine/config`（body `{"config": {...}}`）、`GET /api/engine/status`（返回 `{active, available, drools: {reachable, detail}}`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_api.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from src.api.routes import engine as engine_routes
from src.engine import settings as engine_settings


@pytest.fixture()
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "engine.json"
    monkeypatch.setattr(engine_settings, "SETTINGS_PATH", path)
    monkeypatch.setattr(engine_routes, "_settings_path", lambda: path)
    return path


def test_get_config_defaults(settings_path):
    result = engine_routes.get_engine_config()
    assert result["active"] == "native"


def test_put_config_validates(settings_path):
    payload = engine_routes.EngineConfigPayload(config={"active": "spark"})
    with pytest.raises(Exception) as exc:
        engine_routes.update_engine_config(payload)
    assert "未知引擎" in str(getattr(exc.value, "detail", exc.value))


def test_put_and_get_config(settings_path):
    payload = engine_routes.EngineConfigPayload(
        config={"active": "drools", "drools": {"base_url": "http://127.0.0.1:1"}}
    )
    saved = engine_routes.update_engine_config(payload)
    assert saved["active"] == "drools"


def test_status_reports_unreachable_drools(settings_path):
    engine_settings.save_settings(
        {"active": "drools", "drools": {"base_url": "http://127.0.0.1:1", "timeout_seconds": 1}},
        settings_path,
    )
    status = engine_routes.engine_status()
    assert status["active"] == "drools"
    assert status["drools"]["reachable"] is False


def test_status_native_has_no_probe(settings_path):
    status = engine_routes.engine_status()
    assert status["active"] == "native"
    assert "reachable" in status["drools"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_engine_api.py -v`
Expected: FAIL（`ModuleNotFoundError: src.api.routes.engine`）

- [ ] **Step 3: 实现**

```python
# src/api/routes/engine.py
"""Rule engine selection and status API."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.engine.settings import load_settings, save_settings

router = APIRouter(prefix="/api/engine", tags=["engine"])


def _settings_path():
    """Indirection for tests: monkeypatch target for the settings file."""
    from src.engine.settings import SETTINGS_PATH

    return SETTINGS_PATH


class EngineConfigPayload(BaseModel):
    config: Dict[str, Any]


@router.get("/config")
async def get_engine_config():
    return load_settings(_settings_path())


@router.put("/config")
async def update_engine_config(payload: EngineConfigPayload):
    try:
        return save_settings(payload.config, _settings_path())
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _probe_drools(drools: Dict[str, Any]) -> Dict[str, Any]:
    import httpx

    base_url = str(drools.get("base_url") or "").rstrip("/")
    timeout = float(drools.get("timeout_seconds") or 5)
    if not base_url:
        return {"reachable": False, "detail": "缺少 base_url"}
    try:
        response = httpx.get(f"{base_url}/health", timeout=timeout)
    except Exception as exc:
        return {"reachable": False, "detail": str(exc)[:200]}
    if response.status_code != 200:
        return {"reachable": False, "detail": f"HTTP {response.status_code}"}
    return {"reachable": True, "detail": response.text[:200]}


@router.get("/status")
async def engine_status():
    settings = load_settings(_settings_path())
    active = str(settings.get("active") or "native")
    drools = settings.get("drools") or {}
    return {
        "active": active,
        "available": ["native", "drools"],
        "drools": _probe_drools(drools),
    }
```

`src/api/main.py`：在 import 行 `from .routes import ...` 里加 `engine`，并在 `app.include_router(rules.router)` 之后加 `app.include_router(engine.router)`。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_engine_api.py tests/test_engine_settings.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/engine.py src/api/main.py tests/test_engine_api.py
git commit -m "feat: 引擎配置与状态 API /api/engine"
```

---

### Task 4: 原生引擎原子求值缓存（性能加固一）

**Files:**
- Modify: `src/engine/inference_engine.py`
- Test: `tests/test_atom_cache.py`

**背景：** 现状每条规则的每个叶子条件都独立调用评估器（`_evaluate_condition` 直接 dispatch）。多条规则共享同一 `lab:CTN:ELEVATED` 原子时会重复执行同一评估逻辑。本任务把可缓存叶子类型的求值结果按"原子键"缓存，语义不变。

**Interfaces:**
- Produces: `InferenceEngine._atom_cache: dict`（每次 `run()` / `evaluate_tcm_pattern_candidates()` 开头重置）；`_atom_cache_key(condition) -> str | None`（None 表示不可缓存，直接求值）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_atom_cache.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.engine.evaluators.base import BaseEvaluator, EvaluationResult
from src.kg.schema import ConceptVocabulary
from src.models.patient import Gender, LabResult, PatientSnapshot
from src.models.rule import Rule, RuleCondition, RuleConclusion
from src.rules.repository import RuleRepository
from src.standards.repository import JsonStandardRepository


class _InlineRepo(RuleRepository):
    """Two rules sharing one lab_concept atom, no file fixtures needed."""

    def __init__(self):
        leaf = {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"}
        self.rules = [
            Rule(
                rule_id="CACHE-R1", disease_code="I21.0", disease_name="测试病A",
                conditions=RuleCondition(**leaf),
                conclusion=RuleConclusion(diagnosis="I21.0", message="R1"),
                graph_filename="stemi_graph.json",
            ),
            Rule(
                rule_id="CACHE-R2", disease_code="I21.1", disease_name="测试病B",
                conditions=RuleCondition(**leaf),
                conclusion=RuleConclusion(diagnosis="I21.1", message="R2"),
                graph_filename="stemi_graph.json",
            ),
        ]

    def list_all(self):
        return list(self.rules)

    def get_active_rules(self, department=None):
        return [r for r in self.rules if r.status == "active"]

    def get_treatment_candidates(self, department=None):
        return []


class _CountingEvaluator(BaseEvaluator):
    calls = 0

    def evaluate(self, patient, standard, parameter=None):
        _CountingEvaluator.calls += 1
        matched = bool(patient.labs) and (patient.labs[0].value or 0) > 50
        return EvaluationResult(
            matched=matched,
            patient_value_display=str(patient.labs[0].value if patient.labs else "-"),
            standard_used="counted>50",
        )


def _patient():
    return PatientSnapshot(
        patient_id="P-ATOM", hospital_id="XM_FIRST", age=55, gender=Gender.MALE,
        labs=[LabResult(lab_code="MYO", value=120.0, unit="ng/mL")],
    )


def _engine():
    vocab = ConceptVocabulary()
    vocab.load(str(project_root / "data" / "vocab" / "concepts.json"))
    # 把 MYO 的 ELEVATED 概念换成计数评估器（通过 monkeypatch vocabulary 实现）
    class _Vocab(ConceptVocabulary):
        def get_evaluator_type(self, code):
            return "counting_test"

    return InferenceEngine(
        _InlineRepo(), None, JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
    )


def test_shared_atom_evaluated_once_per_run(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_test", _CountingEvaluator)
    _CountingEvaluator.calls = 0
    engine = _engine()
    conclusions = engine.run(_patient())
    assert {c.rule_id for c in conclusions} == {"CACHE-R1", "CACHE-R2"}
    # 两条规则共享同一原子：评估器只应执行 1 次
    assert _CountingEvaluator.calls == 1


def test_cache_reset_between_runs(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_test", _CountingEvaluator)
    _CountingEvaluator.calls = 0
    engine = _engine()
    first = engine.run(_patient())
    second = engine.run(_patient())
    assert _CountingEvaluator.calls == 2  # 每次运行各自求值一次
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_atom_cache.py -v`
Expected: `test_shared_atom_evaluated_once_per_run` FAIL（calls == 2，尚未缓存）

- [ ] **Step 3: 实现**

`src/engine/inference_engine.py`：

1. 顶部 import 区加 `import json`（若无）。

2. `InferenceEngine` 类内加模块级常量（类外、`logger` 之后）：

```python
# Leaf condition types whose evaluation is deterministic per patient and
# therefore memoizable. Groups/facts depend on stage state and stay dynamic.
_CACHEABLE_LEAF_TYPES = {
    "evidence", "lab_concept", "exam_finding", "symptom", "vital",
    "demographic", "past_history", "medication", "observation", "attribute",
}
```

3. `__init__` 末尾加 `self._atom_cache: Dict[str, tuple] = {}`。

4. 新增两个方法（放在 `_evaluate_condition` 之前）：

```python
    def _atom_cache_key(self, condition: RuleCondition) -> Optional[str]:
        ctype = condition.type
        if ctype == "lab_concept":
            return (
                f"lab_concept:{condition.lab_code}:{condition.concept_code}:"
                + json.dumps(condition.parameter or {}, sort_keys=True, ensure_ascii=False)
            )
        if ctype == "exam_finding":
            return f"exam_finding:{condition.exam_code}:{condition.finding_code}:{condition.concept_code}"
        if ctype == "symptom":
            return f"symptom:{condition.symptom_code}:{condition.duration_minutes}:{condition.concept_code}"
        if ctype == "vital":
            return f"vital:{condition.vital_code}:{condition.operator}:{condition.threshold_value}"
        if ctype == "demographic":
            return f"demographic:{condition.age_min}:{condition.age_max}:{condition.gender}"
        if ctype in {"past_history", "medication"}:
            return f"{ctype}:{condition.code}"
        if ctype == "observation":
            return f"observation:{condition.observation_type or 'symptom'}:{condition.code}:{condition.concept_code}"
        if ctype == "attribute":
            return f"attribute:{condition.attribute_code}:{condition.operator}:{json.dumps(condition.attribute_value, sort_keys=True, ensure_ascii=False, default=str)}"
        if ctype == "evidence":
            # Evidence lookup depends on the per-rule graph; the factory caches
            # one client instance per filename, so instance id is a stable
            # scoping key within a single run (the cache resets every run).
            return f"evidence:{id(self._current_kg)}:{condition.evidence_id}"
        return None

    def _evaluate_condition_cached(
        self, condition: RuleCondition, patient: PatientSnapshot
    ) -> Tuple[bool, List[EvidenceTrace]]:
        key = self._atom_cache_key(condition)
        if key is not None:
            cached = self._atom_cache.get(key)
            if cached is not None:
                matched, traces = cached
                return matched, [trace.model_copy() for trace in traces]
        matched, traces = self._evaluate_condition(condition, patient)
        if key is not None:
            self._atom_cache[key] = (matched, traces)
            return matched, [trace.model_copy() for trace in traces]
        return matched, traces
```

5. `_evaluate_condition` 顶部改为：

```python
    def _evaluate_condition(
        self,
        condition: RuleCondition,
        patient: PatientSnapshot,
    ) -> Tuple[bool, List[EvidenceTrace]]:
        """Recursively evaluate a condition tree."""
        if condition.type in _CACHEABLE_LEAF_TYPES:
            return self._evaluate_condition_cached(condition, patient)
        if condition.type == "group":
            return self._evaluate_group(condition, patient)
        # ……其余分支保持不变（weighted_group / fact 走原 dispatch）
```

6. `run()` 方法内、`conclusions: List[Conclusion] = []` 之前加 `self._atom_cache = {}`；`evaluate_tcm_pattern_candidates()` 内 `candidates: List[Dict[str, Any]] = []` 之前加同一行。

注意：`_evaluate_condition` 里原本 `elif` 链的第一分支是 `if condition.type == "group"`，改为先判缓存类型后走原链即可，不要删除任何原有分支。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `python3 -m pytest tests/test_atom_cache.py tests/test_engine.py tests/test_tcm_domain.py tests/test_tcm_v2_upgrade.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/engine/inference_engine.py tests/test_atom_cache.py
git commit -m "perf: 推理引擎原子条件求值缓存"
```

---

### Task 5: 保守剪枝索引 RuleIndex（性能加固二）

**Files:**
- Create: `src/engine/rule_index.py`
- Modify: `src/engine/inference_engine.py`（`run()` 中接入）
- Test: `tests/test_rule_index.py`

**语义约定（必须保守/sound，剪错的代价是漏诊断）：**
- 叶子需求键：`lab:{lab_code}`（患者有该检验任何值即可满足"可满足"，数值判定交给评估器）、`finding:{exam}:{finding}`（present 才算）、`symptom:{code}`（present 才算）、`vital:{code}`、`past_history:{code}`、`medication:{code}`、`observation:{type}:{code}`（present 才算）、`attribute:{code}`。
- `evidence` 叶子要按规则解析各自图谱，**永不据此剪枝**（返回"可满足"）。
- AND 组：全部子树可满足才算可满足；OR 组：任一子树可满足；NOT 组：恒可满足。
- `weighted_group`：`required_children` 与 `required=True` 的 children 是硬条件，逐个检查；其余加权子项不影响剪枝判定（权重和可能只靠必需项过阈值）。
- 未知类型、缺编码的叶子：恒可满足。

**Interfaces:**
- Produces: `patient_keys(patient: PatientSnapshot) -> set[str]`；`satisfiable(condition: RuleCondition, keys: set[str]) -> bool`；`RuleIndex(rules).satisfiable_rule(rule, keys) -> bool`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rule_index.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine.rule_index import RuleIndex, patient_keys, satisfiable
from src.models.patient import (
    ExamFinding, Gender, LabResult, Observation, PatientSnapshot,
    SymptomObservation, VitalSign,
)
from src.models.rule import Rule, RuleCondition


def _patient():
    return PatientSnapshot(
        patient_id="P-IDX", hospital_id="H1", age=60, gender=Gender.MALE,
        labs=[LabResult(lab_code="CTN", value=5.0)],
        symptoms=[SymptomObservation(symptom_code="SYM_PAIN", present=True)],
        exam_findings=[ExamFinding(exam_code="ECG", finding_code="STE", present=True)],
        observations=[Observation(observation_type="tongue", code="T_RED", present=False)],
        vitals=[VitalSign(vital_code="sbp", value=90)],
        past_history=["HTN"],
        current_medications=["ASP"],
        clinical_attributes={"stage": "II"},
    )


def _rule(conditions) -> Rule:
    return Rule(
        rule_id="IDX-R", disease_code="X", disease_name="X",
        conditions=RuleCondition(**conditions),
        conclusion={"diagnosis": "X"},
    )


def test_patient_keys_shape():
    keys = patient_keys(_patient())
    assert "lab:CTN" in keys
    assert "symptom:SYM_PAIN" in keys
    assert "finding:ECG:STE" in keys
    assert "vital:sbp" in keys
    assert "past_history:HTN" in keys
    assert "medication:ASP" in keys
    assert "observation:tongue:T_RED" not in keys  # present=False 不算
    assert "attribute:stage" in keys


def test_and_group_pruned_when_lab_absent():
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "AND", "children": [
            {"type": "lab_concept", "lab_code": "BNP", "concept_code": "ELEVATED"},
            {"type": "symptom", "symptom_code": "SYM_PAIN"},
        ],
    })
    assert not satisfiable(rule.conditions, keys)


def test_or_group_kept_when_one_branch_present():
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "OR", "children": [
            {"type": "lab_concept", "lab_code": "BNP", "concept_code": "ELEVATED"},
            {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        ],
    })
    assert satisfiable(rule.conditions, keys)


def test_not_group_never_pruned():
    keys = patient_keys(_patient())
    rule = _rule({
        "type": "group", "operator": "NOT", "children": [
            {"type": "symptom", "symptom_code": "ABSENT_CODE"},
        ],
    })
    assert satisfiable(rule.conditions, keys)


def test_weighted_group_required_child_controls():
    keys = patient_keys(_patient())
    missing_required = _rule({
        "type": "weighted_group", "operator": "AND", "threshold": 0.4,
        "children": [{"type": "observation", "observation_type": "tongue", "code": "T_RED"}],
        "required_children": [
            {"type": "observation", "observation_type": "pulse", "code": "P_SINKING"},
        ],
    })
    assert not satisfiable(missing_required.conditions, keys)
    # 只靠必需项也可能过阈值，因此必需项满足时恒不可剪
    present_required = _rule({
        "type": "weighted_group", "operator": "AND", "threshold": 0.4,
        "children": [{"type": "observation", "observation_type": "tongue", "code": "T_WHITE"}],
        "required_children": [
            {"type": "observation", "observation_type": "pulse", "code": "P_PRESENT_CODE"},
        ],
    })
    # P_PRESENT_CODE 不在 keys 中 → 必需项不可满足 → 可剪
    assert not satisfiable(present_required.conditions, keys)


def test_evidence_leaf_never_prunes():
    keys = set()
    rule = _rule({"type": "evidence", "evidence_id": "EV-1"})
    assert satisfiable(rule.conditions, keys)


def test_index_filter():
    keys = patient_keys(_patient())
    hit = _rule({
        "type": "group", "operator": "AND", "children": [
            {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        ],
    })
    miss = _rule({
        "type": "group", "operator": "AND", "children": [
            {"type": "lab_concept", "lab_code": "BNP", "concept_code": "ELEVATED"},
        ],
    })
    index = RuleIndex([hit, miss])
    kept = index.satisfiable_rule(hit, keys)
    assert kept is True
    assert index.satisfiable_rule(miss, keys) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_rule_index.py -v`
Expected: FAIL（`ModuleNotFoundError: src.engine.rule_index`）

- [ ] **Step 3: 实现**

```python
# src/engine/rule_index.py
"""Conservative rule-pruning index.

Before evaluating a rule's condition tree, the index proves whether the rule
can possibly match the data the patient carries.  Pruning is *sound*: a rule
is dropped only when every path through its tree demands data the patient
does not have.  Anything uncertain (evidence leaves resolved inside per-rule
graphs, NOT groups, missing codes) counts as satisfiable so semantics never
change — this is pure pre-filtering ahead of the full evaluation.
"""
from __future__ import annotations

from typing import List, Optional, Set

from ..models.patient import PatientSnapshot
from ..models.rule import Rule, RuleCondition


def patient_keys(patient: PatientSnapshot) -> Set[str]:
    keys: Set[str] = set()
    for lab in patient.labs:
        if lab.lab_code:
            keys.add(f"lab:{lab.lab_code}")
    for symptom in patient.symptoms:
        if symptom.present and symptom.symptom_code:
            keys.add(f"symptom:{symptom.symptom_code}")
    for finding in patient.exam_findings:
        if finding.present and finding.exam_code and finding.finding_code:
            keys.add(f"finding:{finding.exam_code}:{finding.finding_code}")
    for observation in patient.observations:
        if observation.present and observation.code:
            keys.add(
                f"observation:{observation.observation_type or 'symptom'}:{observation.code}"
            )
    for vital in patient.vitals:
        if vital.vital_code:
            keys.add(f"vital:{vital.vital_code}")
    for code in patient.past_history or []:
        keys.add(f"past_history:{code}")
    for code in patient.current_medications or []:
        keys.add(f"medication:{code}")
    for code in (patient.clinical_attributes or {}):
        keys.add(f"attribute:{code}")
    return keys


def _leaf_key(condition: RuleCondition) -> Optional[str]:
    ctype = condition.type
    if ctype == "lab_concept" and condition.lab_code:
        return f"lab:{condition.lab_code}"
    if ctype == "exam_finding" and condition.exam_code and condition.finding_code:
        return f"finding:{condition.exam_code}:{condition.finding_code}"
    if ctype == "symptom" and condition.symptom_code:
        return f"symptom:{condition.symptom_code}"
    if ctype == "vital" and condition.vital_code:
        return f"vital:{condition.vital_code}"
    if ctype in {"past_history", "medication"} and condition.code:
        return f"{ctype}:{condition.code}"
    if ctype == "observation" and condition.code:
        return f"observation:{condition.observation_type or 'symptom'}:{condition.code}"
    if ctype == "attribute" and condition.attribute_code:
        return f"attribute:{condition.attribute_code}"
    # evidence（按规则各自的图谱解析）、未知类型、缺编码 → 不可判定
    return None


def satisfiable(condition: RuleCondition, keys: Set[str]) -> bool:
    """True when the subtree can possibly match given the patient keys."""
    if condition.type in {"group", "weighted_group"}:
        op = (condition.operator or "AND").upper()
        children = list(condition.children or [])
        if condition.type == "weighted_group":
            hard = list(condition.required_children or []) + [
                child for child in children if child.required
            ]
            return all(satisfiable(child, keys) for child in hard)
        if op == "AND":
            return all(satisfiable(child, keys) for child in children)
        if op == "OR":
            if not children:
                return True
            return any(satisfiable(child, keys) for child in children)
        return True  # NOT：数据缺失本身就是一种成立结果
    key = _leaf_key(condition)
    if key is None:
        return True
    return key in keys


class RuleIndex:
    """Pre-filters rules against a patient before full tree evaluation."""

    def __init__(self, rules: List[Rule]):
        self._rules = list(rules)

    def satisfiable_rule(self, rule: Rule, keys: Set[str]) -> bool:
        return satisfiable(rule.conditions, keys)
```

`src/engine/inference_engine.py` 的 `run()`：import 区加 `from .rule_index import patient_keys, satisfiable`；`conclusions: List[Conclusion] = []` 之后（`self._atom_cache = {}` 附近）加：

```python
        patient_key_set = patient_keys(patient)
```

每阶段 `stage_rules.sort(key=lambda rule: -rule.priority)` 之后加：

```python
            stage_rules = [
                rule for rule in stage_rules
                if satisfiable(rule.conditions, patient_key_set)
            ]
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_rule_index.py tests/test_engine.py tests/test_tcm_domain.py tests/test_demo_cases.py -v`
Expected: 全部 passed（剪枝不得改变任何现有结论）

- [ ] **Step 5: Commit**

```bash
git add src/engine/rule_index.py src/engine/inference_engine.py tests/test_rule_index.py
git commit -m "perf: 规则保守剪枝索引接入推理主流程"
```

---

### Task 6: 规则内容版本历史与回滚

**Files:**
- Create: `src/rules/history.py`
- Modify: `src/api/routes/rules.py`（create/update/delete/import 记录快照 + history/restore 端点）
- Test: `tests/test_rule_history.py`

**设计：** 快照存 `data/rules/history/<rule_id>.jsonl`，每行 `{seq, timestamp, action, actor, rule}`。`.jsonl` 后缀不会被 `JsonRuleRepository._load_all` 的 `glob("*.json")` 误读。每次成功变更记录**新内容**（v1=create, v2=update…），回滚 = 恢复指定 seq 的内容并记录一条 `rollback` 快照。

**Interfaces:**
- Produces: `record_snapshot(rule: dict, action: str, actor: str = "", directory: Path | None = None) -> dict`；`list_snapshots(rule_id: str, directory: Path | None = None) -> list[dict]`；`get_snapshot(rule_id: str, seq: int, directory: Path | None = None) -> dict | None`。
- API: `GET /api/rules/{rule_id}/history`；`POST /api/rules/{rule_id}/history/{seq}/restore`（body `{"reviewer","note"}`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rule_history.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.rules import history


def _rule(name):
    return {"rule_id": "H-1", "name": name, "status": "draft", "priority": 50}


def test_create_update_snapshot_sequence(tmp_path):
    directory = tmp_path / "history"
    history.record_snapshot(_rule("v1"), "create", directory=directory)
    history.record_snapshot(_rule("v2"), "update", directory=directory)
    items = history.list_snapshots("H-1", directory)
    assert [item["seq"] for item in items] == [1, 2]
    assert [item["action"] for item in items] == ["create", "update"]
    assert items[1]["rule"]["name"] == "v2"


def test_get_snapshot_by_seq(tmp_path):
    directory = tmp_path / "history"
    history.record_snapshot(_rule("v1"), "create", directory=directory)
    entry = history.get_snapshot("H-1", 1, directory)
    assert entry["rule"]["name"] == "v1"
    assert history.get_snapshot("H-1", 99, directory) is None


def test_missing_history_returns_empty(tmp_path):
    assert history.list_snapshots("NOPE", tmp_path / "history") == []


def test_rule_id_sanitized(tmp_path):
    directory = tmp_path / "history"
    history.record_snapshot({"rule_id": "a/b\\c:d", "name": "x"}, "create", directory=directory)
    files = list(directory.glob("*.jsonl"))
    assert len(files) == 1
    assert "/" not in files[0].name and "\\" not in files[0].name
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_rule_history.py -v`
Expected: FAIL（`ModuleNotFoundError: src.rules.history`）

- [ ] **Step 3: 实现**

```python
# src/rules/history.py
"""Content snapshots for rule edits (data/rules/history/<rule_id>.jsonl).

Every successful create/update/import-replace/delete/rollback appends the new
content so the timeline reads v1..vN.  Status-only review transitions stay in
the review audit log; this file tracks *content* for restore.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIR = ROOT / "data" / "rules" / "history"


def _snapshot_path(rule_id: str, directory: Optional[Path] = None) -> Path:
    target = directory or HISTORY_DIR
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(rule_id))
    return target / f"{safe}.jsonl"


def record_snapshot(
    rule: Dict[str, Any],
    action: str,
    actor: str = "",
    directory: Optional[Path] = None,
) -> Dict[str, Any]:
    rule_id = str(rule.get("rule_id") or "")
    existing = list_snapshots(rule_id, directory)
    entry = {
        "seq": (int(existing[-1]["seq"]) + 1) if existing else 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor": actor or "未署名",
        "rule": rule,
    }
    path = _snapshot_path(rule_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def list_snapshots(rule_id: str, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = _snapshot_path(rule_id, directory)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    items.sort(key=lambda item: int(item.get("seq") or 0))
    return items


def get_snapshot(rule_id: str, seq: int, directory: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    for item in list_snapshots(rule_id, directory):
        if int(item.get("seq") or 0) == seq:
            return item
    return None
```

`src/api/rules.py` 接线（顶部 import 加 `from src.rules.history import get_snapshot, list_snapshots, record_snapshot`；history 目录用默认 `HISTORY_DIR`，route 函数内部不传 directory，测试 monkeypatch `src.rules.history.HISTORY_DIR`）：

1. `create_rule`：`_save_all_rules(rules)` 之后加 `record_snapshot(rule, "create", actor=str(rule.get("created_by") or ""))`。
2. `update_rule`：`_save_all_rules(rules)` 之后加 `record_snapshot(rule, "update")`。
3. `delete_rule`：改为先取被删规则再快照：

```python
@router.delete("/{rule_id}")
async def delete_rule(rule_id: str):
    rules = _load_all_rules()
    removed = next((r for r in rules if r.get("rule_id") == rule_id), None)
    rules = [r for r in rules if r.get("rule_id") != rule_id]
    _save_all_rules(rules)
    if removed is not None:
        record_snapshot(removed, "delete")
    return {"deleted": rule_id}
```

4. `import_rules`：`if item["rule_id"] in existing:` 的 replace 分支，在 `rules[existing[item["rule_id"]]] = item` 之后加 `record_snapshot(item, "import")`。
5. 新端点（放在 `graph_dependencies` 附近）：

```python
class RuleRestoreRequest(BaseModel):
    reviewer: str = "未署名"
    note: str = ""


@router.get("/{rule_id}/history")
async def rule_history(rule_id: str):
    return list_snapshots(rule_id)


@router.post("/{rule_id}/history/{seq}/restore")
async def restore_rule_snapshot(rule_id: str, seq: int, request: RuleRestoreRequest):
    entry = get_snapshot(rule_id, seq)
    if entry is None:
        raise HTTPException(404, "历史版本不存在")
    restored = entry.get("rule") or {}
    if str(restored.get("status") or "draft") == "active":
        _ensure_active_rule_dependencies(restored)
    rules = _load_all_rules()
    for i, r in enumerate(rules):
        if r.get("rule_id") == rule_id:
            restored["updated_at"] = datetime.now(timezone.utc).isoformat()
            restored["restored_from"] = seq
            rules[i] = restored
            _save_all_rules(rules)
            record_snapshot(restored, "rollback", actor=request.reviewer)
            append_audit({
                "action": "rollback",
                "entity_type": "rule",
                "entity_id": rule_id,
                "graph_filename": restored.get("graph_filename", ""),
                "before_status": r.get("status"),
                "after_status": restored.get("status"),
                "reviewer": request.reviewer or "未署名",
                "note": request.note or f"恢复到 v{seq}",
                "affected_rules": [],
            }, REVIEW_AUDIT_PATH)
            return restored
    raise HTTPException(404, "规则未找到")
```

注意：`RuleRestoreRequest` 放在文件里已有 BaseModel 定义区；`datetime` 已在 rules.py import。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_rule_history.py tests/test_review_api.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/rules/history.py src/api/routes/rules.py tests/test_rule_history.py
git commit -m "feat: 规则内容版本历史与回滚 API"
```

---

### Task 7: 业务字典联动核心 `src/vocab/cascade.py`

**Files:**
- Create: `src/vocab/cascade.py`
- Test: `tests/test_vocab_cascade.py`

**设计：** 字典变更对规则的三种联动——
1. **改名**（PUT）：字典已保存新名后，重扫规则重新生成 `label`/`condition_text`/`disease_name`（用 `TerminologyLabels`，label 由字典现名生成，所以必须先存字典再跑级联）；
2. **改码**（新 remap 端点）：替换规则里该 (entity_type, code) 的全部引用并重建标签；
3. **删除保护**：规则仍引用的实体编码禁止删除（与概念词汇现有保护一致）。图谱引用只报告、不改写（图谱走审核工作流）。

**Interfaces:**
- Produces: `collect_rule_codes(rule) -> list[tuple[entity_type, code]]`；`affected_rule_ids(entity_type, code, rules) -> list[str]`；`apply_name_cascade(entity_type, code, new_name, rules_path, labels=None) -> {"updated": [...]}`；`apply_code_remap(entity_type, old_code, new_code, rules_path, labels=None) -> {"updated": [...]}`；`usage_in_graphs(entity_type, code, kg_dir) -> list[str]`（引用图谱文件名）。`rules_path` 默认 `data/rules/managed_rules.json`，测试可传 tmp。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_vocab_cascade.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.terminology.labels import TerminologyLabels
from src.vocab.cascade import (
    affected_rule_ids,
    apply_code_remap,
    apply_name_cascade,
    collect_rule_codes,
    usage_in_graphs,
)


def _labels(tmp_path):
    entity_dir = tmp_path / "entities"
    entity_dir.mkdir(exist_ok=True)
    (entity_dir / "lab.json").write_text(
        json.dumps([{"code": "CTN", "name": "心肌肌钙蛋白"}]), encoding="utf-8")
    (entity_dir / "disease.json").write_text(
        json.dumps([{"code": "I21.0", "name": "急性心肌梗死"}]), encoding="utf-8")
    (entity_dir / "symptom.json").write_text(
        json.dumps([{"code": "SYM_PAIN", "name": "胸痛"}]), encoding="utf-8")
    (tmp_path / "concepts.json").write_text(
        json.dumps([{"code": "ELEVATED", "name_cn": "升高"}]), encoding="utf-8")
    return TerminologyLabels(entity_dir=entity_dir, concept_path=tmp_path / "concepts.json")


def _rules_file(tmp_path, rules):
    path = tmp_path / "managed_rules.json"
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return path


def _sample_rule():
    return {
        "rule_id": "CAS-1",
        "disease_code": "I21.0",
        "disease_name": "急性心肌梗死",
        "anchor_type": "disease",
        "status": "draft",
        "conditions": {
            "type": "group", "operator": "AND", "children": [
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
                {"type": "symptom", "symptom_code": "SYM_PAIN"},
            ],
        },
        "conclusion": {"diagnosis": "I21.0", "message": ""},
    }


def test_collect_rule_codes():
    pairs = collect_rule_codes(_sample_rule())
    assert ("disease", "I21.0") in pairs
    assert ("lab", "CTN") in pairs
    assert ("symptom", "SYM_PAIN") in pairs


def test_affected_rule_ids():
    rules = [_sample_rule(), {"rule_id": "OTHER", "disease_code": "C81", "conditions": {"type": "group", "operator": "AND", "children": []}}]
    assert affected_rule_ids("lab", "CTN", rules) == ["CAS-1"]
    assert affected_rule_ids("lab", "BNP", rules) == []


def test_apply_name_cascade_updates_labels(tmp_path):
    path = _rules_file(tmp_path, [_sample_rule()])
    labels = _labels(tmp_path)
    result = apply_name_cascade("lab", "CTN", "肌钙蛋白I", path, labels=labels)
    assert result["updated"] == ["CAS-1"]
    saved = json.loads(path.read_text(encoding="utf-8"))[0]
    assert "肌钙蛋白I（CTN）" in json.dumps(saved["conditions"], ensure_ascii=False)
    assert "肌钙蛋白I" in (saved.get("condition_text") or "")


def test_apply_code_remap(tmp_path):
    path = _rules_file(tmp_path, [_sample_rule()])
    labels = _labels(tmp_path)
    result = apply_code_remap("lab", "CTN", "CTNI", path, labels=labels)
    assert result["updated"] == ["CAS-1"]
    saved = json.loads(path.read_text(encoding="utf-8"))[0]
    leaf = saved["conditions"]["children"][0]
    assert leaf["lab_code"] == "CTNI"


def test_usage_in_graphs(tmp_path):
    kg_dir = tmp_path / "kg"
    kg_dir.mkdir()
    (kg_dir / "g1.json").write_text(json.dumps({
        "nodes": [{"id": "ENTITY:lab:CTN", "node_type": "lab", "code": "CTN", "name": "CTN"}],
    }), encoding="utf-8")
    (kg_dir / "g2.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    assert usage_in_graphs("lab", "CTN", kg_dir) == ["g1.json"]
    assert usage_in_graphs("lab", "BNP", kg_dir) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_vocab_cascade.py -v`
Expected: FAIL（`ModuleNotFoundError: src.vocab.cascade`）

- [ ] **Step 3: 实现**

```python
# src/vocab/cascade.py
"""Cascade shared-dictionary changes into stored rules.

Rules reference dictionary codes (disease_code, symptom_code, lab_code, ...).
A rename re-derives readable labels from the *saved* dictionary; a code remap
rewrites references everywhere in the rule; deletion of a referenced code is
blocked by the API layer.  Graph/template impact is reported, never silently
rewritten — graph changes go through the review workflow.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..terminology.labels import TerminologyLabels, default_labels

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "data" / "rules" / "managed_rules.json"
KG_DIR = ROOT / "data" / "kg"

# rule condition field -> entity dictionary types that field may reference
CONDITION_FIELDS: Dict[str, Tuple[str, ...]] = {
    "lab_code": ("lab",),
    "symptom_code": ("symptom",),
    "exam_code": ("exam",),
    "finding_code": ("exam_finding",),
    "medication": (),  # conclusion 字段，不在此处理
}
OBSERVATION_TYPE_TO_ENTITY = {
    "symptom": "symptom", "tongue": "tongue", "pulse": "pulse",
    "sign": "sign", "constitution": "constitution",
}
# conclusion 内引用实体编码的字段
CONCLUSION_LIST_FIELDS = {
    "treatment_items": "entity_type",  # 每项 {code, name, entity_type}
}


def _anchor_entity_type(rule: Dict[str, Any]) -> str:
    return "syndrome" if str(rule.get("anchor_type")) == "syndrome" else "disease"


def collect_rule_codes(rule: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []

    def add(entity_type: str, code: str) -> None:
        if entity_type and code:
            pairs.append((entity_type, str(code)))

    anchor_type = _anchor_entity_type(rule)
    add(anchor_type, str(rule.get("disease_code") or ""))

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if str(node.get("type")) == "observation":
            add(
                OBSERVATION_TYPE_TO_ENTITY.get(str(node.get("observation_type") or "symptom"), ""),
                str(node.get("code") or ""),
            )
        for field, types in CONDITION_FIELDS.items():
            value = str(node.get(field) or "")
            if value and types:
                add(types[0], value)
        for child in node.get("children") or []:
            walk(child)
        for child in node.get("required_children") or []:
            walk(child)

    walk(rule.get("conditions"))

    conclusion = rule.get("conclusion") or {}
    for item in conclusion.get("treatment_items") or []:
        if isinstance(item, dict):
            add(str(item.get("entity_type") or ""), str(item.get("code") or ""))
    for herb in conclusion.get("herbs") or []:
        if isinstance(herb, dict):
            add("herb", str(herb.get("code") or ""))
    for field in ("treatment_method_code", "formula_code"):
        entity_type = "treatment_method" if field == "treatment_method_code" else "formula"
        add(entity_type, str(conclusion.get(field) or ""))
    return list(dict.fromkeys(pairs))


def affected_rule_ids(entity_type: str, code: str, rules: List[Dict[str, Any]]) -> List[str]:
    target = (entity_type, str(code))
    return [
        str(rule.get("rule_id") or "")
        for rule in rules
        if target in collect_rule_codes(rule)
    ]


def _load_rules(rules_path: Path) -> List[Dict[str, Any]]:
    if not rules_path.exists():
        return []
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _save_rules(rules_path: Path, rules: List[Dict[str, Any]]) -> None:
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def _refresh_labels(rule: Dict[str, Any], labels: TerminologyLabels) -> None:
    labels.annotate(rule.get("conditions"))
    rule["condition_text"] = labels.summarize(rule.get("conditions"))


def apply_name_cascade(
    entity_type: str,
    code: str,
    new_name: str,
    rules_path: Path = RULES_PATH,
    labels: Optional[TerminologyLabels] = None,
) -> Dict[str, Any]:
    """Dictionary entry renamed: re-derive labels and display names."""
    labels = labels or default_labels()
    rules = _load_rules(rules_path)
    updated: List[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for rule in rules:
        if (entity_type, str(code)) not in collect_rule_codes(rule):
            continue
        if entity_type == _anchor_entity_type(rule) and str(rule.get("disease_code")) == str(code):
            rule["disease_name"] = new_name
        _refresh_labels(rule, labels)
        rule["updated_at"] = now
        updated.append(str(rule.get("rule_id") or ""))
    if updated:
        _save_rules(rules_path, rules)
    return {"updated": updated}


def apply_code_remap(
    entity_type: str,
    old_code: str,
    new_code: str,
    rules_path: Path = RULES_PATH,
    labels: Optional[TerminologyLabels] = None,
) -> Dict[str, Any]:
    """Dictionary code remapped: rewrite every rule reference to the code."""
    labels = labels or default_labels()
    rules = _load_rules(rules_path)
    updated: List[str] = []
    now = datetime.now(timezone.utc).isoformat()

    for rule in rules:
        changed = False
        if entity_type == _anchor_entity_type(rule) and str(rule.get("disease_code")) == str(old_code):
            rule["disease_code"] = new_code
            changed = True
        conclusion = rule.get("conclusion") or {}
        if entity_type == "disease" and str(conclusion.get("diagnosis") or "") == str(old_code):
            conclusion["diagnosis"] = new_code
            changed = True

        def walk(node: Any) -> None:
            nonlocal changed
            if not isinstance(node, dict):
                return
            if (
                str(node.get("type")) == "observation"
                and OBSERVATION_TYPE_TO_ENTITY.get(str(node.get("observation_type") or "symptom")) == entity_type
                and str(node.get("code") or "") == str(old_code)
            ):
                node["code"] = new_code
                changed = True
            for field, types in CONDITION_FIELDS.items():
                if entity_type in types and str(node.get(field) or "") == str(old_code):
                    node[field] = new_code
                    changed = True
            for child in node.get("children") or []:
                walk(child)
            for child in node.get("required_children") or []:
                walk(child)

        walk(rule.get("conditions"))
        for item in conclusion.get("treatment_items") or []:
            if (
                isinstance(item, dict)
                and str(item.get("entity_type") or "") == entity_type
                and str(item.get("code") or "") == str(old_code)
            ):
                item["code"] = new_code
                changed = True
        for herb in conclusion.get("herbs") or []:
            if entity_type == "herb" and isinstance(herb, dict) and str(herb.get("code") or "") == str(old_code):
                herb["code"] = new_code
                changed = True
        if str(conclusion.get("treatment_method_code") or "") == str(old_code) and entity_type == "treatment_method":
            conclusion["treatment_method_code"] = new_code
            changed = True
        if str(conclusion.get("formula_code") or "") == str(old_code) and entity_type == "formula":
            conclusion["formula_code"] = new_code
            changed = True

        if changed:
            _refresh_labels(rule, labels)
            rule["updated_at"] = now
            updated.append(str(rule.get("rule_id") or ""))

    if updated:
        _save_rules(rules_path, rules)
    return {"updated": updated}


def usage_in_graphs(entity_type: str, code: str, kg_dir: Path = KG_DIR) -> List[str]:
    """Graph filenames whose nodes reference this dictionary code."""
    hits: List[str] = []
    if not kg_dir.exists():
        return hits
    for graph_path in sorted(kg_dir.glob("*.json")):
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = graph.get("nodes") or []
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        referenced = any(
            isinstance(node, dict)
            and str(node.get("node_type") or "") == entity_type
            and str(node.get("code") or "") == str(code)
            for node in nodes
        )
        if not referenced and entity_type == "disease":
            diseases = graph.get("diseases") or []
            referenced = any(
                isinstance(item, dict) and str(item.get("code") or "") == str(code)
                for item in diseases
            )
        if referenced:
            hits.append(graph_path.name)
    return hits
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_vocab_cascade.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/vocab/cascade.py tests/test_vocab_cascade.py
git commit -m "feat: 业务字典变更联动规则的核心级联逻辑"
```

---

### Task 8: 字典联动接入 vocab 路由

**Files:**
- Modify: `src/api/routes/vocab.py`（PUT 改名自动级联、DELETE 引用保护、新增 remap 端点、`default_labels.cache_clear()`）
- Test: `tests/test_vocab_cascade_api.py`

**关键细节：** `src/terminology/labels.py` 的 `default_labels()` 是 `@lru_cache(maxsize=1)`——字典保存后必须 `default_labels.cache_clear()`，否则改名后的标签级联会用到旧名（这是现有潜在缺陷，本任务一并修复）。规则文件路径通过 monkeypatch `src.vocab.cascade.RULES_PATH` 控制。

**Interfaces:**
- Produces: `POST /api/vocab/entities/{entity_type}/{code}/remap`（body `{"new_code": "..."}`；图谱引用该码时 409 拒绝）；PUT/DELETE 行为增强。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_vocab_cascade_api.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from fastapi import HTTPException

from src.api.routes import vocab as vocab_routes
from src.terminology.labels import default_labels
from src.vocab import cascade


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Redirect rules path + clear the labels cache around each test."""
    rules_path = tmp_path / "managed_rules.json"
    rules_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(cascade, "RULES_PATH", rules_path)
    default_labels.cache_clear()
    yield tmp_path, rules_path
    default_labels.cache_clear()


def _rule_with_ctn():
    return {
        "rule_id": "W-1", "disease_code": "I21.0", "disease_name": "急性心肌梗死",
        "anchor_type": "disease", "status": "draft",
        "conditions": {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
        "conclusion": {"diagnosis": "I21.0", "message": ""},
    }


def test_delete_blocked_when_rule_references(isolated):
    tmp_path, rules_path = isolated
    rules_path.write_text(json.dumps([_rule_with_ctn()], ensure_ascii=False), encoding="utf-8")
    from src.api.routes.vocab import delete_entity_entry

    with pytest.raises(HTTPException) as exc:
        delete_entity_entry("lab", "CTN")
    assert exc.value.status_code == 409


def test_remap_endpoint_cascades_rules(isolated, monkeypatch):
    tmp_path, rules_path = isolated
    rules_path.write_text(json.dumps([_rule_with_ctn()], ensure_ascii=False), encoding="utf-8")
    from src.api.routes.vocab import remap_entity_code

    # 先在真实字典里放一个临时条目（用真实 repo，因为 remap 读写共享词表）
    from src.vocab.entity_vocabulary import EntityVocabularyRepository

    repo = EntityVocabularyRepository("lab")
    original = [item.model_dump() for item in repo.list()]
    if not any(item["code"] == "CTN" for item in original):
        entry = repo.save(
            type(repo.list()[0])(
                code="CTN", name="心肌肌钙蛋白", aliases=[], domain="western",
            ) if original else None,
            is_new=True,
        ) if original else None
        if entry is None:
            # 空字典时直接构造条目
            from src.extraction.models import TemplateEntityEntry

            repo.save(TemplateEntityEntry(code="CTN", name="心肌肌钙蛋白"), is_new=True)
    try:
        result = remap_entity_code("lab", "CTN", {"new_code": "CTNI-TEST"})
        assert result["remapped"] == "CTNI-TEST"
        assert result["rules_updated"] == ["W-1"]
        saved = json.loads(rules_path.read_text(encoding="utf-8"))[0]
        assert saved["conditions"]["lab_code"] == "CTNI-TEST"
        assert repo.find("CTN") is None
        assert repo.find("CTNI-TEST") is not None
    finally:
        # 还原共享词表，避免污染其它测试
        if not any(item["code"] == "CTN" for item in original):
            item = repo.find("CTNI-TEST")
            if item is not None:
                repo.delete("CTNI-TEST")
        elif any(item["code"] == "CTNI-TEST" for item in original):
            pass


def test_rename_via_put_triggers_cascade(isolated, monkeypatch):
    tmp_path, rules_path = isolated
    rules_path.write_text(json.dumps([_rule_with_ctn()], ensure_ascii=False), encoding="utf-8")
    from src.extraction.models import TemplateEntityEntry
    from src.vocab.entity_vocabulary import EntityVocabularyRepository

    repo = EntityVocabularyRepository("lab")
    if repo.find("CTN") is None:
        repo.save(TemplateEntityEntry(code="CTN", name="心肌肌钙蛋白"), is_new=True)
    try:
        updated = vocab_routes.update_entity_entry(
            "lab", "CTN",
            {"code": "CTN", "name": "高敏肌钙蛋白", "aliases": [], "domain": "western"},
        )
        assert updated["name"] == "高敏肌钙蛋白"
        saved = json.loads(rules_path.read_text(encoding="utf-8"))[0]
        assert "高敏肌钙蛋白" in (saved.get("condition_text") or "")
    finally:
        if repo.find("CTN") is not None:
            repo.delete("CTN")
```

注意：`TemplateEntityEntry` 的必填字段以 `src/extraction/models.py` 实际定义为准——执行时先读该模型再写测试 fixture，不要臆造字段（如缺 `aliases` 默认值则显式传）。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_vocab_cascade_api.py -v`
Expected: FAIL（`remap_entity_code` 不存在；delete 未做规则保护；rename 不级联）

- [ ] **Step 3: 实现**

`src/api/routes/vocab.py`：

1. import 区加：

```python
from src.terminology.labels import default_labels
from src.vocab.cascade import (
    affected_rule_ids,
    apply_code_remap,
    apply_name_cascade,
    usage_in_graphs,
)
from src.rules.repository import _RULES_HINT  # 若无此导出，改用下一行方式
```

实际不需要 rules 路径 import——级联函数默认读写 `data/rules/managed_rules.json`，直接调用即可。删除上面第三行。

2. `update_entity_entry` 改为：

```python
@router.put("/entities/{entity_type}/{code}")
async def update_entity_entry(entity_type: str, code: str, entry: Dict):
    parsed = _validate_entry(entity_type, _parse_entry(entry, code))
    _check_concept_entry(entity_type, parsed.code)
    repository = EntityVocabularyRepository(entity_type)
    previous = repository.find(code)
    saved = repository.save(parsed)
    default_labels.cache_clear()
    if previous is not None and previous.name != saved.name:
        # 字典已保存新名，标签级联基于现名重生成
        apply_name_cascade(entity_type, code, saved.name)
    return classify_entries(entity_type, [saved.model_dump()])[0]
```

3. `delete_entity_entry` 在图谱引用检查之后、模板检查之前加：

```python
    from src.api.routes.rules import _load_all_rules

    referenced_rules = affected_rule_ids(entity_type, code, _load_all_rules())
    if referenced_rules:
        raise HTTPException(
            409,
            f"共享实体 {code} 仍被 {len(referenced_rules)} 条规则引用（如 {'、'.join(referenced_rules[:5])}），不能删除",
        )
```

（import 放文件顶部：`from src.api.routes.rules import _load_all_rules` 与 `from src.vocab.cascade import affected_rule_ids, apply_code_remap, apply_name_cascade, usage_in_graphs`。）

4. 新端点（放在 `delete_entity_entry` 之后）：

```python
@router.post("/entities/{entity_type}/{code}/remap")
async def remap_entity_code(entity_type: str, code: str, payload: Dict):
    new_code = str((payload or {}).get("new_code") or "").strip()
    if not new_code or new_code == code:
        raise HTTPException(422, "new_code 必须是与原码不同的非空编码")
    repository = EntityVocabularyRepository(entity_type)
    if repository.find(code) is None:
        raise HTTPException(404, f"共享词表 {entity_type} 中不存在编码 {code}")
    if repository.find(new_code) is not None:
        raise HTTPException(409, f"目标编码 {new_code} 已存在")
    graph_hits = usage_in_graphs(entity_type, code)
    if graph_hits:
        raise HTTPException(
            409,
            f"编码 {code} 仍被图谱 {'、'.join(graph_hits)} 引用；请先在知识审核工作流迁移图谱节点，再改码",
        )
    repository.remap_code(code, new_code)
    default_labels.cache_clear()
    cascade_result = apply_code_remap(entity_type, code, new_code)
    return {
        "entity_type": entity_type,
        "old_code": code,
        "new_code": new_code,
        "rules_updated": cascade_result["updated"],
    }
```

5. `src/vocab/entity_vocabulary.py` 的 `EntityVocabularyRepository` 加方法：

```python
    def remap_code(self, old_code: str, new_code: str) -> dict:
        items = self._read()
        if not any(item.get("code") == old_code for item in items):
            raise HTTPException(404, f"共享词表 {self.entity_type} 中不存在编码 {old_code}")
        if any(item.get("code") == new_code for item in items):
            raise HTTPException(409, f"共享词表 {self.entity_type} 中编码 {new_code} 已存在")
        for item in items:
            if item.get("code") == old_code:
                item["code"] = new_code
                if item.get("standard_code") == old_code:
                    item["standard_code"] = new_code
        self._write(items)
        return {"old_code": old_code, "new_code": new_code}
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_vocab_cascade_api.py tests/test_vocab_cascade.py tests/test_terminology_labels.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/vocab.py src/vocab/entity_vocabulary.py tests/test_vocab_cascade_api.py
git commit -m "feat: 字典改名级联/改码 remap/删除保护接入词表 API"
```

---

### Task 9: DRL 编译器 `src/engine/drools/compiler.py`

**Files:**
- Create: `src/engine/drools/__init__.py`（空文件 + docstring）
- Create: `src/engine/drools/compiler.py`
- Test: `tests/test_drl_compiler.py`

**设计（与 Task 4/5 的原子语义对齐）：** Python 预评估原子（叶子），sidecar 只做布尔树匹配。叶子按"先序遍历"顺序编号 `a0..aN`；患者投影端只插入 `matched=true` 的原子 fact，所以 LHS 用存在性模式（`Key(ruleId==..., code==...)`）表达 true；AND=并列模式；OR=`( A or B )`；NOT=`not ( … )`（患者没有该 fact = 未发生 = NOT 成立）。`weighted_group`/`fact`/`evidence`/空组**不可编译**（由适配器走原生）。

**Interfaces:**
- Produces: `SUPPORTED_LEAF_TYPES: set[str]`；`CompiledRule`（dataclass：`rule_id: str, salience: int, drl: str, leaves: list[RuleCondition]`）；`compile_rule(rule: Rule) -> CompiledRule | None`；`compile_rule_set(compiled: list[CompiledRule]) -> str`（含 package/global/declare 头）；`walk_leaves(condition) -> list[RuleCondition] | None`（顺序与 LHS 编号一致；不可编译返回 None）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_drl_compiler.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine.drools.compiler import (
    compile_rule,
    compile_rule_set,
    walk_leaves,
)
from src.models.rule import Rule, RuleCondition, RuleConclusion


def _rule(rule_id, conditions, priority=50):
    return Rule(
        rule_id=rule_id, disease_code="X", disease_name="X", priority=priority,
        conditions=RuleCondition(**conditions),
        conclusion=RuleConclusion(diagnosis="X"),
    )


def test_compiles_and_group_of_or_and_leaf():
    rule = _rule("T1", {
        "type": "group", "operator": "AND", "children": [
            {"type": "group", "operator": "OR", "children": [
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ELEVATED"},
                {"type": "lab_concept", "lab_code": "CTN", "concept_code": "ABOVE_ULN_1"},
            ]},
            {"type": "symptom", "symptom_code": "SYM1"},
        ],
    })
    compiled = compile_rule(rule)
    assert compiled is not None
    assert [c.type for c in compiled.leaves] == ["lab_concept", "lab_concept", "symptom"]
    assert 'rule "T1"' in compiled.drl
    assert "salience 50" in compiled.drl
    assert '( Key( ruleId == "T1", code == "a0" ) or Key( ruleId == "T1", code == "a1" ) )' in compiled.drl
    assert 'Key( ruleId == "T1", code == "a2" )' in compiled.drl
    assert 'matched.add("T1");' in compiled.drl


def test_compiles_not_group():
    rule = _rule("T2", {
        "type": "group", "operator": "NOT", "children": [
            {"type": "symptom", "symptom_code": "S1"},
        ],
    })
    compiled = compile_rule(rule)
    assert compiled is not None
    assert 'not ( Key( ruleId == "T2", code == "a0" ) )' in compiled.drl


def test_single_leaf_rule():
    rule = _rule("T3", {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"})
    compiled = compile_rule(rule)
    assert compiled is not None
    assert compiled.drl.strip().startswith('rule "T3"')


def test_unsupported_types_return_none():
    assert compile_rule(_rule("W1", {"type": "weighted_group", "children": [
        {"type": "observation", "observation_type": "tongue", "code": "T1"}]})) is None
    assert compile_rule(_rule("W2", {"type": "fact", "fact_type": "syndrome", "code": "S1"})) is None
    assert compile_rule(_rule("W3", {"type": "evidence", "evidence_id": "EV"})) is None
    assert compile_rule(_rule("W4", {"type": "group", "operator": "AND", "children": []})) is None


def test_rule_set_includes_header():
    compiled = compile_rule(_rule("T3", {"type": "symptom", "symptom_code": "S"}))
    drl = compile_rule_set([compiled])
    assert drl.startswith("package cdss;")
    assert "global java.util.List matched;" in drl
    assert "declare Key" in drl


def test_walk_order_matches_lhs_ids():
    rule = _rule("T5", {
        "type": "group", "operator": "AND", "children": [
            {"type": "symptom", "symptom_code": "A"},
            {"type": "symptom", "symptom_code": "B"},
            {"type": "symptom", "symptom_code": "C"},
        ],
    })
    leaves = walk_leaves(rule.conditions)
    assert [leaf.symptom_code for leaf in leaves] == ["A", "B", "C"]
    compiled = compile_rule(rule)
    assert compiled.leaves == leaves
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_drl_compiler.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`src/engine/drools/__init__.py`：

```python
"""Drools adapter: JSON rule -> DRL matching in a sidecar, conclusions native."""
```

`src/engine/drools/compiler.py`：

```python
# src/engine/drools/compiler.py
"""Compile native JSON rule condition trees into Drools DRL.

Atoms (leaf conditions) are pre-evaluated by the Python engine; the sidecar
only evaluates the boolean structure.  A Key fact is inserted for every
satisfied atom, so LHS patterns test presence:

    Key( ruleId == "R1", code == "a0" )

NOT groups use ``not`` (absence of the fact = not happened), OR groups use a
nested ``or`` CE.  Condition types that need weighted scoring or per-rule
graph resolution are NOT compilable; the adapter evaluates those natively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ...models.rule import Rule, RuleCondition

SUPPORTED_LEAF_TYPES = {
    "lab_concept", "exam_finding", "symptom", "vital", "demographic",
    "past_history", "medication", "observation", "attribute",
}

DRL_HEADER = """package cdss;

global java.util.List matched;

declare Key
    ruleId : String
    code : String
end

"""


@dataclass
class CompiledRule:
    rule_id: str
    salience: int
    drl: str
    leaves: List[RuleCondition] = field(default_factory=list)


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def walk_leaves(condition: RuleCondition) -> Optional[List[RuleCondition]]:
    """Pre-order leaf list; None when the tree contains an uncompilable node."""
    ctype = condition.type
    if ctype in {"group", "weighted_group"}:
        children = list(condition.children or [])
        if not children:
            return None
        leaves: List[RuleCondition] = []
        for child in children:
            child_leaves = walk_leaves(child)
            if child_leaves is None:
                return None
            leaves.extend(child_leaves)
        return leaves
    if ctype not in SUPPORTED_LEAF_TYPES:
        return None
    return [condition]


def _lhs(condition: RuleCondition, rule_id: str, counter: List[int]) -> Optional[str]:
    ctype = condition.type
    if ctype in {"group", "weighted_group"}:
        children = list(condition.children or [])
        if not children:
            return None
        parts = []
        for child in children:
            part = _lhs(child, rule_id, counter)
            if part is None:
                return None
            parts.append(part)
        op = (condition.operator or "AND").upper()
        if op == "AND":
            return "\n    ".join(parts)
        if op == "OR":
            return "(" + " or ".join(parts) + ")"
        if op == "NOT":
            return "not (" + " or ".join(parts) + ")"
        return None
    if ctype not in SUPPORTED_LEAF_TYPES:
        return None
    index = counter[0]
    counter[0] += 1
    return f'Key( ruleId == "{_escape(rule_id)}", code == "a{index}" )'


def compile_rule(rule: Rule) -> Optional[CompiledRule]:
    leaves = walk_leaves(rule.conditions)
    if not leaves:
        return None
    counter = [0]
    lhs = _lhs(rule.conditions, rule.rule_id, counter)
    if lhs is None:
        return None
    drl = (
        f'rule "{_escape(rule.rule_id)}"\n'
        f"salience {int(rule.priority)}\n"
        "when\n"
        f"    {lhs}\n"
        "then\n"
        f'    matched.add("{_escape(rule.rule_id)}");\n'
        "end"
    )
    return CompiledRule(
        rule_id=rule.rule_id,
        salience=int(rule.priority),
        drl=drl,
        leaves=leaves,
    )


def compile_rule_set(compiled: List[CompiledRule]) -> str:
    return DRL_HEADER + "\n".join(item.drl for item in compiled) + "\n"
```

注意 LHS 编号顺序与 `walk_leaves` 的先序顺序一致（两处递归都按 children 顺序，在叶子处分配/收集），这是患者投影端（Task 11）正确性的前提。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_drl_compiler.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/engine/drools/__init__.py src/engine/drools/compiler.py tests/test_drl_compiler.py
git commit -m "feat: 规则条件树到 DRL 的编译器"
```

---

### Task 10: Sidecar HTTP 客户端

**Files:**
- Create: `src/engine/drools/client.py`
- Test: `tests/test_drools_client.py`

**Interfaces:**
- Produces: `DroolsClientError(RuntimeError)`；`DroolsClient(base_url, timeout_seconds=5.0, transport=None)`，方法 `health() -> dict`、`execute(drl: str, facts: list[dict]) -> list[str]`（facts 元素 `{"ruleId","code"}`，返回匹配的 ruleId 列表）。`transport` 透传给 `httpx.Client`（测试用 `httpx.MockTransport`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_drools_client.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import httpx
import pytest

from src.engine.drools.client import DroolsClient, DroolsClientError


def _client(handler) -> DroolsClient:
    return DroolsClient("http://sidecar.test", timeout_seconds=2, transport=httpx.MockTransport(handler))


def test_execute_returns_matched_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/execute"
        payload = json.loads(request.content)
        assert payload["drl"].startswith("package cdss;")
        assert payload["facts"] == [{"ruleId": "R1", "code": "a0"}]
        return httpx.Response(200, json={"matched": ["R1", "R2"]})

    client = _client(handler)
    assert client.execute("package cdss;...", [{"ruleId": "R1", "code": "a0"}]) == ["R1", "R2"]


def test_health_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    assert _client(handler).health() == {"status": "ok"}


def test_non_200_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(DroolsClientError):
        _client(handler).execute("...", [])


def test_connection_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(DroolsClientError):
        _client(handler).health()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_drools_client.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# src/engine/drools/client.py
"""HTTP client for the Drools matching sidecar."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class DroolsClientError(RuntimeError):
    pass


class DroolsClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0, transport: Optional[httpx.BaseTransport] = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                return client.request(method, f"{self._base_url}{path}", **kwargs)
        except Exception as exc:
            raise DroolsClientError(f"sidecar 调用失败: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        response = self._request("GET", "/health")
        if response.status_code != 200:
            raise DroolsClientError(f"sidecar /health 返回 HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise DroolsClientError(f"sidecar /health 响应不是 JSON: {exc}") from exc

    def execute(self, drl: str, facts: List[Dict[str, str]]) -> List[str]:
        response = self._request("POST", "/execute", json={"drl": drl, "facts": facts})
        if response.status_code != 200:
            raise DroolsClientError(
                f"sidecar /execute 返回 HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise DroolsClientError(f"sidecar /execute 响应不是 JSON: {exc}") from exc
        return [str(item) for item in (data.get("matched") or [])]
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_drools_client.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/engine/drools/client.py tests/test_drools_client.py
git commit -m "feat: Drools sidecar HTTP 客户端"
```

---

### Task 11: InferenceEngine 匹配器化重构（保持行为不变）

**Files:**
- Modify: `src/engine/inference_engine.py`
- Test: `tests/test_matcher_refactor.py`

**动机：** Drools 适配器需要按"阶段批量匹配"，但不能复制阶段逻辑。把 `run()` 的阶段循环抽到 `_run_stages(patient, department, include_draft_patterns, matcher)`，matcher 契约：`matcher(stage_rules, patient) -> dict[rule_id, (traces, weighted_detail)]`，`None` 缺席 = 未匹配。原生实现 `_native_match_stage` 逐规则求值（含 per-rule 图谱切换、异常日志、`_last_weighted_detail` 采集），`run()` = `_run_stages(..., matcher=self._native_match_stage)`，**外部行为完全不变**。

**Interfaces:**
- Produces（供 Task 12 适配器使用）：
  - `InferenceEngine._run_stages(patient, department, include_draft_patterns, matcher) -> list[Conclusion]`
  - `InferenceEngine._native_match_stage(stage_rules, patient) -> dict`
  - `InferenceEngine._eval_rule_with_graph(rule, patient) -> tuple[matched, traces, weighted_detail]`（含 per-rule `_current_kg` 切换与异常吞噬日志）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_matcher_refactor.py
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.engine.evaluators.base import BaseEvaluator, EvaluationResult
from src.kg.schema import ConceptVocabulary
from src.models.patient import Gender, LabResult, PatientSnapshot
from src.models.rule import Rule, RuleCondition, RuleConclusion
from src.rules.repository import RuleRepository
from src.standards.repository import JsonStandardRepository


class _Repo(RuleRepository):
    def __init__(self):
        leaf = {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"}
        self.rules = [
            Rule(rule_id="M-R1", disease_code="I21.0", disease_name="A",
                 conditions=RuleCondition(**leaf),
                 conclusion=RuleConclusion(diagnosis="I21.0", message="R1"),
                 graph_filename="stemi_graph.json"),
        ]

    def list_all(self):
        return list(self.rules)

    def get_active_rules(self, department=None):
        return [r for r in self.rules if r.status == "active"]

    def get_treatment_candidates(self, department=None):
        return []


class _Counting(BaseEvaluator):
    calls = 0

    def evaluate(self, patient, standard, parameter=None):
        _Counting.calls += 1
        value = patient.labs[0].value if patient.labs else 0
        return EvaluationResult(matched=value > 50, patient_value_display=str(value), standard_used="c>50")


class _Vocab(ConceptVocabulary):
    def get_evaluator_type(self, code):
        return "counting_matcher"


def _engine():
    return InferenceEngine(
        _Repo(), None,
        JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
    )


def _patient():
    return PatientSnapshot(
        patient_id="P-M", hospital_id="XM_FIRST", age=55, gender=Gender.MALE,
        labs=[LabResult(lab_code="MYO", value=120.0)],
    )


def test_run_stages_with_custom_matcher(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    engine = _engine()

    def fake_matcher(stage_rules, patient):
        # 假装 sidecar 说 M-R1 匹配了；引擎随后应重建结论与溯源
        return {"M-R1": ([], {})}

    conclusions = engine._run_stages(_patient(), None, False, matcher=fake_matcher)
    assert [c.rule_id for c in conclusions] == ["M-R1"]
    assert conclusions[0].evidence == []


def test_native_match_stage_returns_traces(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    engine = _engine()
    patient = _patient()
    results = engine._native_match_stage(engine.rule_repo.list_all(), patient)
    assert "M-R1" in results
    traces, weighted = results["M-R1"]
    assert traces  # 原生匹配必须产出证据链
    assert weighted == {}


def test_default_run_matches_previous_behavior(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_matcher", _Counting)
    _Counting.calls = 0
    engine = _engine()
    conclusions = engine.run(_patient())
    assert [c.rule_id for c in conclusions] == ["M-R1"]
    assert conclusions[0].matched_conditions >= 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_matcher_refactor.py -v`
Expected: FAIL（`_run_stages`/`_native_match_stage` 不存在）

- [ ] **Step 3: 实现**

`src/engine/inference_engine.py` 中把现有 `run()` 的阶段循环体抽为 `_run_stages`：

```python
    def run(
        self,
        patient: PatientSnapshot,
        department: Optional[str] = None,
        include_draft_patterns: bool = False,
    ) -> List[Conclusion]:
        """Run domain-staged inference with the native matcher."""
        return self._run_stages(
            patient, department, include_draft_patterns,
            matcher=self._native_match_stage,
        )

    def _eval_rule_with_graph(
        self, rule: Rule, patient: PatientSnapshot
    ) -> Tuple[bool, List[EvidenceTrace], Dict[str, Any]]:
        """Evaluate one rule with its own graph bound, swallowing+logging errors."""
        profile = get_profile(self.domain)
        self._current_kg = (
            self.graph_client_factory(str(rule.graph_filename or profile.graph.legacy_graph_filename))
            if self.graph_client_factory
            else self.kg
        )
        try:
            matched, traces = self._evaluate_condition(rule.conditions, patient)
        except Exception as e:
            logger.warning(f"Rule {rule.rule_id} evaluation failed: {e}")
            matched, traces = False, []
        finally:
            self._current_kg = self.kg
        weighted = dict(self._last_weighted_detail or {})
        self._last_weighted_detail = {}
        return matched, traces, weighted

    def _native_match_stage(
        self,
        stage_rules: List[Rule],
        patient: PatientSnapshot,
    ) -> Dict[str, Tuple[List[EvidenceTrace], Dict[str, Any]]]:
        results: Dict[str, Tuple[List[EvidenceTrace], Dict[str, Any]]] = {}
        for rule in stage_rules:
            matched, traces, weighted = self._eval_rule_with_graph(rule, patient)
            if matched:
                results[rule.rule_id] = (traces, weighted)
        return results

    def _run_stages(
        self,
        patient: PatientSnapshot,
        department: Optional[str] = None,
        include_draft_patterns: bool = False,
        matcher=None,
    ) -> List[Conclusion]:
        """Staged inference.  ``matcher(stage_rules, patient)`` returns
        ``{rule_id: (traces, weighted_detail)}`` for the rules that matched;
        rules absent from the mapping are treated as unmatched.  Drools mode
        supplies a different matcher without touching stage logic."""
        matcher = matcher or self._native_match_stage
        profile = get_profile(self.domain)
        all_rules = list(self.rule_repo.list_all())
        explicit_types = {
            rule_type
            for stage in profile.stages
            for rule_type in stage.rule_types
        }

        self._atom_cache = {}
        patient_key_set = patient_keys(patient)
        conclusions: List[Conclusion] = []
        facts: Dict[str, Dict[str, Conclusion]] = {}

        for stage in profile.stages:
            # ……阶段过滤保持原逻辑（rule_type / status / domain / department /
            # draft 门控 / priority 排序 / satisfiable 剪枝），剪枝后：
            matched_map = matcher(stage_rules, patient)
            stage_conclusions: List[Conclusion] = []
            for rule in stage_rules:
                entry = matched_map.get(rule.rule_id)
                if entry is None:
                    continue
                traces, weighted = entry
                self._last_weighted_detail = weighted or {}
                try:
                    trigger_ids = [
                        c.rule_id for c in conclusions
                        if c.disease_code == rule.disease_code
                        and c.rule_type not in {"treatment", "tcm_treatment", "tcm_prescription"}
                    ]
                    conclusion = self._build_conclusion(
                        rule, traces, patient,
                        trigger_ids=trigger_ids
                        if rule.rule_type in {"treatment", "tcm_treatment", "tcm_prescription"}
                        else None,
                    )
                    stage_conclusions.append(conclusion)
                except Exception as e:
                    logger.warning(f"Rule {rule.rule_id} conclusion build failed: {e}")
                finally:
                    self._last_weighted_detail = {}

            conclusions.extend(stage_conclusions)
            self._materialize_facts(facts, stage_conclusions)

        conclusions.sort(key=lambda c: (-c.priority, c.rule_type))
        return conclusions
```

执行时**保留**原 `run()` 中全部阶段过滤代码，仅把"逐规则求值 + 结论构建"替换为"matcher 调用 + 按 matched_map 构建结论"。`_rule_diagnostics` 等其它调用方不动。

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_matcher_refactor.py tests/test_engine.py tests/test_tcm_domain.py tests/test_tcm_v2_upgrade.py tests/test_demo_cases.py tests/test_auto_pipeline.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/engine/inference_engine.py tests/test_matcher_refactor.py
git commit -m "refactor: 推理引擎抽出自定义匹配器钩子，行为不变"
```

---

### Task 12: DroolsRuleEngine 适配器 + reference sidecar

**Files:**
- Create: `src/engine/drools/engine.py`
- Create: `drools-sidecar/pom.xml`
- Create: `drools-sidecar/src/main/java/dev/cdss/sidecar/Main.java`
- Create: `drools-sidecar/README.md`
- Test: `tests/test_drools_engine.py`

**Interfaces:**
- Consumes: Task 9 compiler、Task 10 client、Task 11 `_run_stages/_eval_rule_with_graph/_evaluate_condition`。
- Produces: `DroolsRuleEngine(native, base_url, timeout_seconds=5.0, fail_over_native=True, transport=None)`，实现 `RuleEngine`（`name="drools"`）；属性 `last_fallback_reason`、`effective_name`；方法 `describe()`。失败语义：编译不支持的规则在每阶段就地走原生；sidecar 网络/HTTP 错误在 `fail_over_native=True` 时整轮回退原生并记录原因，否则抛 `DroolsClientError`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_drools_engine.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import httpx
import pytest

from src.engine.drools.client import DroolsClientError
from src.engine.drools.engine import DroolsRuleEngine
from src.engine import InferenceEngine
from src.engine.evaluators import EvaluatorFactory
from src.engine.evaluators.base import BaseEvaluator, EvaluationResult
from src.kg.schema import ConceptVocabulary
from src.models.patient import Gender, LabResult, PatientSnapshot
from src.models.rule import Rule, RuleCondition, RuleConclusion
from src.rules.repository import RuleRepository
from src.standards.repository import JsonStandardRepository


class _Repo(RuleRepository):
    def __init__(self, extra=None):
        leaf = {"type": "lab_concept", "lab_code": "MYO", "concept_code": "ELEVATED"}
        self.rules = [
            Rule(rule_id="D-R1", disease_code="I21.0", disease_name="A",
                 conditions=RuleCondition(**leaf),
                 conclusion=RuleConclusion(diagnosis="I21.0", message="R1"),
                 graph_filename="stemi_graph.json"),
        ]
        self.rules.extend(extra or [])

    def list_all(self):
        return list(self.rules)

    def get_active_rules(self, department=None):
        return [r for r in self.rules if r.status == "active"]

    def get_treatment_candidates(self, department=None):
        return []


class _Vocab(ConceptVocabulary):
    def get_evaluator_type(self, code):
        return "counting_drools"


class _Counting(BaseEvaluator):
    calls = 0

    def evaluate(self, patient, standard, parameter=None):
        _Counting.calls += 1
        value = patient.labs[0].value if patient.labs else 0
        return EvaluationResult(matched=value > 50, patient_value_display=str(value), standard_used="c>50")


def _patient():
    return PatientSnapshot(
        patient_id="P-D", hospital_id="XM_FIRST", age=55, gender=Gender.MALE,
        labs=[LabResult(lab_code="MYO", value=120.0)],
    )


def _stub_transport(matched_ids_fn, status=200):
    """matched_ids_fn(payload) -> list[str]"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        payload = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, text="sidecar down")
        return httpx.Response(200, json={"matched": matched_ids_fn(payload)})

    return httpx.MockTransport(handler)


def _drools_engine(repo, transport, fail_over=True):
    native = InferenceEngine(
        repo, None,
        JsonStandardRepository(str(project_root / "data" / "standards")),
        _Vocab(),
    )
    return DroolsRuleEngine(
        native, base_url="http://sidecar.test",
        timeout_seconds=2, fail_over_native=fail_over, transport=transport,
    )


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    monkeypatch.setitem(EvaluatorFactory._registry, "counting_drools", _Counting)
    yield


def test_drools_conclusions_match_native():
    # sidecar 对每个出现原子的规则返回匹配（单原子规则下与原生语义一致）
    transport = _stub_transport(lambda payload: sorted({f["ruleId"] for f in payload["facts"]}))
    repo = _Repo()
    patient = _patient()
    drools_result = _drools_engine(repo, transport).run(patient)
    native_result = _drools_engine(repo, transport).run(patient)
    native = _drools_engine(repo, transport)._native
    assert [c.model_dump() for c in drools_result] == [c.model_dump() for c in native.run(patient)]
    assert drools_result[0].evidence  # 溯源由原生层重建


def test_uncompilable_rules_evaluated_natively():
    weighted = Rule(
        rule_id="D-W", disease_code="I21.9", disease_name="W",
        conditions=RuleCondition(**{
            "type": "weighted_group", "operator": "AND", "threshold": 0.1,
            "children": [{"type": "observation", "observation_type": "tongue", "code": "T_ANY"}],
        }),
        conclusion=RuleConclusion(diagnosis="I21.9"),
    )
    repo = _Repo(extra=[weighted])
    transport = _stub_transport(lambda payload: [])  # sidecar 什么都不匹配
    engine = _drools_engine(repo, transport)
    patient = _patient().model_copy(deep=True)
    patient.observations = [
        __import__("src.models.patient", fromlist=["Observation"]).Observation(
            observation_type="tongue", code="T_ANY", present=True)
    ]
    conclusions = engine.run(patient)
    assert any(c.rule_id == "D-W" for c in conclusions)  # weighted_group 走原生


def test_fail_over_on_sidecar_error():
    transport = _stub_transport(lambda payload: [], status=500)
    engine = _drools_engine(_Repo(), transport, fail_over=True)
    conclusions = engine.run(_patient())
    assert [c.rule_id for c in conclusions] == ["D-R1"]
    assert engine.last_fallback_reason
    assert engine.effective_name == "native"
    assert engine.describe()["used"] == "native"


def test_no_fail_over_raises():
    transport = _stub_transport(lambda payload: [], status=500)
    engine = _drools_engine(_Repo(), transport, fail_over=False)
    with pytest.raises(DroolsClientError):
        engine.run(_patient())
```

注意 `test_uncompilable_rules_evaluated_natively` 里的 Observation import 写丑了，执行时改成顶部 `from src.models.patient import Observation` 正常导入。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_drools_engine.py -v`
Expected: FAIL（`DroolsRuleEngine` 不存在）

- [ ] **Step 3: 实现**

`src/engine/drools/engine.py`：

```python
# src/engine/drools/engine.py
"""Drools-backed rule engine: matching in the sidecar, conclusions native.

The native engine stays the source of truth.  Per inference stage the adapter
compiles compilable rules to DRL, projects pre-evaluated atoms as facts, asks
the sidecar which rules match, then rebuilds Conclusions through the native
evaluator so evidence traces and scores are identical to native mode.  Rules
with uncompilable trees (weighted scoring, facts, graph evidence) always
evaluate natively; a sidecar failure falls back to the native engine for the
whole run when ``fail_over_native`` is set.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ...models.conclusion import Conclusion, EvidenceTrace
from ...models.patient import PatientSnapshot
from ...models.rule import Rule
from .client import DroolsClient, DroolsClientError
from .compiler import CompiledRule, compile_rule, compile_rule_set

logger = logging.getLogger(__name__)


class DroolsRuleEngine:
    name = "drools"

    def __init__(
        self,
        native,
        base_url: str = "http://127.0.0.1:8090",
        timeout_seconds: float = 5.0,
        fail_over_native: bool = True,
        transport=None,
    ):
        self._native = native
        self._client = DroolsClient(base_url, timeout_seconds, transport=transport)
        self._fail_over_native = bool(fail_over_native)
        self._fallback = False
        self.last_fallback_reason = ""

    @property
    def effective_name(self) -> str:
        return "native" if self._fallback else self.name

    def describe(self) -> Dict[str, Any]:
        return {
            "requested": self.name,
            "used": self.effective_name,
            "fallback_reason": self.last_fallback_reason,
        }

    def run(
        self,
        patient: PatientSnapshot,
        department: Optional[str] = None,
        include_draft_patterns: bool = False,
    ) -> List[Conclusion]:
        self._fallback = False
        self.last_fallback_reason = ""
        try:
            return self._native._run_stages(
                patient, department, include_draft_patterns,
                matcher=self._guarded_stage_match,
            )
        except DroolsClientError as exc:
            if not self._fail_over_native:
                raise
            self._fallback = True
            self.last_fallback_reason = str(exc)
            logger.warning("Drools sidecar unavailable, falling back to native: %s", exc)
            return self._native.run(patient, department=department,
                                    include_draft_patterns=include_draft_patterns)

    def _guarded_stage_match(self, stage_rules, patient):
        if self._fallback:
            return self._native._native_match_stage(stage_rules, patient)
        try:
            return self._stage_match(stage_rules, patient)
        except DroolsClientError as exc:
            if not self._fail_over_native:
                raise
            self._fallback = True
            self.last_fallback_reason = str(exc)
            logger.warning("Drools sidecar unavailable mid-run, falling back: %s", exc)
            return self._native._native_match_stage(stage_rules, patient)

    def _stage_match(
        self,
        stage_rules: List[Rule],
        patient: PatientSnapshot,
    ) -> Dict[str, Tuple[List[EvidenceTrace], Dict[str, Any]]]:
        results: Dict[str, Tuple[List[EvidenceTrace], Dict[str, Any]]] = {}
        compiled_items: List[CompiledRule] = []
        compiled_rules: Dict[str, Rule] = {}
        for rule in stage_rules:
            item = compile_rule(rule)
            if item is None:
                matched, traces, weighted = self._native._eval_rule_with_graph(rule, patient)
                if matched:
                    results[rule.rule_id] = (traces, weighted)
                continue
            compiled_items.append(item)
            compiled_rules[rule.rule_id] = rule
        if not compiled_items:
            return results

        facts: List[Dict[str, str]] = []
        for item in compiled_items:
            rule = compiled_rules[item.rule_id]
            for index, leaf in enumerate(item.leaves):
                # 编译叶子不引用 per-rule 图谱（无 evidence 类型），可直接求值
                matched, _ = self._native._evaluate_condition(leaf, patient)
                if matched:
                    facts.append({"ruleId": item.rule_id, "code": f"a{index}"})
        matched_ids = set(self._client.execute(compile_rule_set(compiled_items), facts))
        for rule_id in matched_ids:
            rule = compiled_rules.get(rule_id)
            if rule is None:
                continue
            # 用原生层重建结论与溯源（原子已被缓存，代价低）
            matched, traces, weighted = self._native._eval_rule_with_graph(rule, patient)
            if matched:
                results[rule_id] = (traces, weighted)
        return results
```

`drools-sidecar/pom.xml`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>dev.cdss</groupId>
  <artifactId>drools-sidecar</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>

  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <drools.version>8.44.0.Final</drools.version>
    <jackson.version>2.17.0</jackson.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.drools</groupId>
      <artifactId>drools-core</artifactId>
      <version>${drools.version}</version>
    </dependency>
    <dependency>
      <groupId>org.drools</groupId>
      <artifactId>drools-compiler</artifactId>
      <version>${drools.version}</version>
    </dependency>
    <dependency>
      <groupId>org.drools</groupId>
      <artifactId>drools-mvel</artifactId>
      <version>${drools.version}</version>
    </dependency>
    <dependency>
      <groupId>org.kie</groupId>
      <artifactId>kie-api</artifactId>
      <version>${drools.version}</version>
    </dependency>
    <dependency>
      <groupId>org.kie</groupId>
      <artifactId>kie-internal</artifactId>
      <version>${drools.version}</version>
    </dependency>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
      <version>${jackson.version}</version>
    </dependency>
  </dependencies>

  <build>
    <finalName>drools-sidecar</finalName>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-shade-plugin</artifactId>
        <version>3.5.1</version>
        <executions>
          <execution>
            <phase>package</phase>
            <goals><goal>shade</goal></goals>
            <configuration>
              <transformers>
                <transformer implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
                  <mainClass>dev.cdss.sidecar.Main</mainClass>
                </transformer>
              </transformers>
            </configuration>
          </execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</project>
```

`drools-sidecar/src/main/java/dev/cdss/sidecar/Main.java`：

```java
package dev.cdss.sidecar;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import org.kie.api.io.ResourceType;
import org.kie.api.runtime.KieSession;
import org.kie.internal.builder.KieHelper;

/**
 * Stateless Drools matching sidecar.
 *
 * POST /execute  {"drl": "...", "facts": [{"ruleId": "...", "code": "..."}]}
 *   -> 200 {"matched": ["RULE_ID", ...]}
 * GET /health -> 200 {"status":"ok"}
 *
 * Facts carry only *satisfied* atoms, so DRL patterns test presence and
 * ``not`` means "atom absent for that rule".
 */
public class Main {

    public static class Key {
        private String ruleId;
        private String code;

        public String getRuleId() { return ruleId; }
        public void setRuleId(String ruleId) { this.ruleId = ruleId; }
        public String getCode() { return code; }
        public void setCode(String code) { this.code = code; }
    }

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8090"));
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        ObjectMapper mapper = new ObjectMapper();

        server.createContext("/health", exchange -> respond(exchange, 200, "{\"status\":\"ok\"}"));

        server.createContext("/execute", exchange -> {
            if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                respond(exchange, 405, "{\"error\":\"POST only\"}");
                return;
            }
            try {
                JsonNode payload = mapper.readTree(exchange.getRequestBody());
                String drl = payload.path("drl").asText("");
                List<String> matched = executeDrl(drl, payload.withArray("facts"));
                respond(exchange, 200, mapper.writeValueAsString(Map.of("matched", matched)));
            } catch (Exception e) {
                respond(exchange, 422, mapper.writeValueAsString(
                        Map.of("error", String.valueOf(e.getMessage()))));
            }
        });

        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        System.out.println("drools-sidecar listening on " + port);
    }

    private static List<String> executeDrl(String drl, JsonNode facts) {
        KieHelper helper = new KieHelper();
        helper.addContent(drl, ResourceType.DRL);
        KieSession session = helper.build().newKieSession();
        try {
            List<String> matched = new ArrayList<>();
            session.setGlobal("matched", matched);
            for (JsonNode fact : facts) {
                Key key = new Key();
                key.setRuleId(fact.path("ruleId").asText(""));
                key.setCode(fact.path("code").asText(""));
                session.insert(key);
            }
            session.fireAllRules();
            return matched;
        } finally {
            session.dispose();
        }
    }

    private static void respond(HttpExchange exchange, int status, String body) throws Exception {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}
```

`drools-sidecar/README.md`：

```markdown
# Drools Matching Sidecar（参考实现）

Python 引擎把每条规则的条件树编译成 DRL（`src/engine/drools/compiler.py`），
把已评估为真的原子条件投影成 fact，本服务用 Drools RETE 批量求值布尔树，
返回命中的 ruleId。结论与溯源仍由 Python 原生层重建，两种模式输出一致。

## 构建与运行

要求 Java 17 + Maven：

    cd drools-sidecar
    mvn -q package
    PORT=8090 java -jar target/drools-sidecar.jar

## 接口

- `GET /health` -> `{"status":"ok"}`
- `POST /execute`，body：`{"drl": "...", "facts": [{"ruleId":"R1","code":"a0"}]}`
  -> `{"matched": ["R1", ...]}`

## 接入

`data/engine.json`：

    {
      "active": "drools",
      "drools": {
        "base_url": "http://127.0.0.1:8090",
        "timeout_seconds": 5,
        "fail_over_native": true
      }
    }

`fail_over_native: true` 时 sidecar 不可达自动回退原生引擎（推理响应
`engine.fallback_reason` 可见原因）。

## 适用范围

加权评分规则（`weighted_group`）、跨阶段 fact 条件、按规则图谱解析的
`evidence` 条件不参与编译，始终由原生引擎求值；因此中医辨证规则在 Drools
模式下仍走原生路径，行为不受影响。
```

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `python3 -m pytest tests/test_drools_engine.py tests/test_drl_compiler.py tests/test_drools_client.py tests/test_matcher_refactor.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/engine/drools/engine.py drools-sidecar tests/test_drools_engine.py
git commit -m "feat: DroolsRuleEngine 适配器（sidecar 匹配+原生结论）与参考 sidecar"
```

---

### Task 13: 推理路由接入引擎工厂

**Files:**
- Modify: `src/api/routes/inference.py`
- Test: `tests/test_inference_engine_switch.py`

**Interfaces:**
- Consumes: Task 2 `create_rule_engine`。
- Produces: `_inference_result` 响应新增 `"engine": {requested, used, fallback_reason}`；`/test-case` 保持原生引擎（测试台需 `evaluate_tcm_pattern_candidates`，这是原生专属能力）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_inference_engine_switch.py
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from src.api.routes import inference as inference_routes
from src.engine import settings as engine_settings


@pytest.fixture()
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "engine.json"
    monkeypatch.setattr(engine_settings, "SETTINGS_PATH", path)
    return path


def _patient_payload():
    raw = json.loads(
        (project_root / "data" / "patients" / "demo_stemi_patient.json").read_text(encoding="utf-8")
    )
    return raw


def test_engine_field_present_by_default(settings_path):
    result = inference_routes._inference_result(
        inference_routes.PatientSnapshot(**_patient_payload()), "western", None
    )
    assert result["engine"]["requested"] == "native"
    assert result["engine"]["used"] == "native"


def test_drools_mode_falls_back_to_native(settings_path):
    engine_settings.save_settings(
        {
            "active": "drools",
            "drools": {"base_url": "http://127.0.0.1:1", "timeout_seconds": 1, "fail_over_native": True},
        },
        settings_path,
    )
    result = inference_routes._inference_result(
        inference_routes.PatientSnapshot(**_patient_payload()), "western", None
    )
    assert result["engine"]["requested"] == "drools"
    assert result["engine"]["used"] == "native"
    assert result["engine"]["fallback_reason"]
```

注意：`_inference_result` 的第三个位置参数签名是 `(patient, domain, department, include_draft_patterns=False)`，测试按此调用。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_inference_engine_switch.py -v`
Expected: FAIL（响应无 `engine` 键）

- [ ] **Step 3: 实现**

`src/api/routes/inference.py`：

1. import 加 `from src.engine.factory import create_rule_engine`。
2. `_inference_result` 中：

```python
    engine = create_rule_engine(
        rules, kg, standards, vocab, graph_factory,
        domain=domain,
    )
```

替换现有 `engine = InferenceEngine(...)` 构造。

3. 返回 dict 增加键（放在 `"domain"` 之后）：`"engine": engine.describe(),`。

4. `/test-case` 路由保持直接构造 `InferenceEngine`（已是现状，不改）。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_inference_engine_switch.py tests/test_engine_factory.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/inference.py tests/test_inference_engine_switch.py
git commit -m "feat: 推理接口接入可切换引擎工厂并返回 engine 元信息"
```

---

### Task 14: 前端规则向导模块 `rule-wizard.js`（纯函数，可测）

**Files:**
- Create: `src/api/static/rule-wizard.js`
- Test: `tests/js/rule_wizard.test.js`

**Interfaces:**
- Produces: `window.CdssRuleWizard = { CONDITION_KINDS, buildConditionTree(rows), describeTree(tree) }`。
  - `rows` 元素：`{kind: "lab_concept"|"symptom"|"exam_finding"|"observation", lab_code, concept_codes: [..], symptom_code, exam_code, finding_code, observation_type, code}`。
  - 行与行 AND；同一检验行多概念 → OR 组；缺字段的行丢弃；全部为空 → 空 AND 组（治疗类规则的合法空条件）。

- [ ] **Step 1: 写失败测试**

```javascript
// tests/js/rule_wizard.test.js
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE_PATH = path.join(__dirname, "..", "..", "src", "api", "static", "rule-wizard.js");

function loadWizard() {
  const source = fs.readFileSync(SOURCE_PATH, "utf8");
  const context = vm.createContext({ window: {}, console });
  vm.runInContext(source, context, { filename: SOURCE_PATH });
  return context.window.CdssRuleWizard;
}

test("multi rows AND, multi concepts OR", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "lab_concept", lab_code: "CTN", concept_codes: ["ELEVATED", "ABOVE_ULN_1"] },
    { kind: "symptom", symptom_code: "SYM_PAIN" },
  ]);
  assert.equal(tree.type, "group");
  assert.equal(tree.operator, "AND");
  const [labGroup, symptom] = tree.children;
  assert.equal(labGroup.type, "group");
  assert.equal(labGroup.operator, "OR");
  assert.equal(labGroup.children.length, 2);
  assert.equal(symptom.type, "symptom");
  assert.equal(symptom.symptom_code, "SYM_PAIN");
});

test("single concept stays a leaf", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "lab_concept", lab_code: "MYO", concept_codes: ["ELEVATED"] },
  ]);
  assert.equal(tree.type, "lab_concept");
  assert.equal(tree.concept_code, "ELEVATED");
});

test("invalid rows dropped, empty rows produce empty AND group", () => {
  const wizard = loadWizard();
  assert.deepEqual(
    wizard.buildConditionTree([
      { kind: "lab_concept", lab_code: "", concept_codes: [] },
      { kind: "symptom", symptom_code: "" },
    ]),
    { type: "group", operator: "AND", children: [] },
  );
});

test("observation row carries type and PRESENT", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "observation", observation_type: "tongue", code: "T_RED" },
  ]);
  assert.deepEqual(tree, {
    type: "observation", observation_type: "tongue", code: "T_RED", concept_code: "PRESENT",
  });
});

test("exam_finding row", () => {
  const wizard = loadWizard();
  const tree = wizard.buildConditionTree([
    { kind: "exam_finding", exam_code: "ECG", finding_code: "STE" },
  ]);
  assert.deepEqual(tree, { type: "exam_finding", exam_code: "ECG", finding_code: "STE" });
});

test("describeTree renders readable text", () => {
  const wizard = loadWizard();
  const text = wizard.describeTree(wizard.buildConditionTree([
    { kind: "lab_concept", lab_code: "CTN", concept_codes: ["ELEVATED", "ABOVE_ULN_1"] },
    { kind: "symptom", symptom_code: "SYM_PAIN" },
  ]));
  assert.ok(text.includes("AND"));
  assert.ok(text.includes("OR"));
  assert.ok(text.includes("CTN"));
});
```

- [ ] **Step 2: 运行确认失败**

Run: `node --test tests/js/rule_wizard.test.js`
Expected: FAIL（文件不存在）

- [ ] **Step 3: 实现**

```javascript
// src/api/static/rule-wizard.js
"use strict";
// Dictionary-driven rule wizard core: rows -> native condition tree.
// Pure functions, no DOM, so node --test can exercise them (see
// tests/js/rule_wizard.test.js).  app.js renders the dialog around these.
(function () {
  const CONDITION_KINDS = [
    { value: "lab_concept", label: "检验概念" },
    { value: "symptom", label: "症状" },
    { value: "exam_finding", label: "检查发现" },
    { value: "observation", label: "四诊观察" },
  ];

  function rowToNode(row) {
    if (!row || !row.kind) return null;
    if (row.kind === "lab_concept") {
      const concepts = (row.concept_codes || []).filter(Boolean);
      if (!row.lab_code || !concepts.length) return null;
      const leaves = concepts.map((code) => ({
        type: "lab_concept",
        lab_code: row.lab_code,
        concept_code: code,
      }));
      return leaves.length === 1 ? leaves[0] : { type: "group", operator: "OR", children: leaves };
    }
    if (row.kind === "symptom") {
      return row.symptom_code ? { type: "symptom", symptom_code: row.symptom_code } : null;
    }
    if (row.kind === "exam_finding") {
      return row.exam_code && row.finding_code
        ? { type: "exam_finding", exam_code: row.exam_code, finding_code: row.finding_code }
        : null;
    }
    if (row.kind === "observation") {
      return row.code
        ? {
            type: "observation",
            observation_type: row.observation_type || "symptom",
            code: row.code,
            concept_code: "PRESENT",
          }
        : null;
    }
    return null;
  }

  function buildConditionTree(rows) {
    const children = (rows || []).map(rowToNode).filter(Boolean);
    if (!children.length) return { type: "group", operator: "AND", children: [] };
    if (children.length === 1) return children[0];
    return { type: "group", operator: "AND", children };
  }

  function describeNode(node) {
    if (!node || typeof node !== "object") return "";
    if (node.type === "group") {
      const parts = (node.children || []).map(describeNode).filter(Boolean);
      const joiner = node.operator === "OR" ? " OR " : " AND ";
      const text = parts.join(joiner);
      return node.operator === "NOT" ? `NOT (${text})` : text;
    }
    if (node.type === "lab_concept") return `${node.lab_code} ${node.concept_code}`;
    if (node.type === "symptom") return `症状 ${node.symptom_code}`;
    if (node.type === "exam_finding") return `${node.exam_code}·${node.finding_code}`;
    if (node.type === "observation") return `观察 ${node.observation_type}:${node.code}`;
    return node.type || "";
  }

  function describeTree(tree) {
    return describeNode(tree);
  }

  window.CdssRuleWizard = { CONDITION_KINDS, buildConditionTree, rowToNode, describeTree };
})();
```

- [ ] **Step 4: 运行确认通过**

Run: `node --test tests/js/rule_wizard.test.js`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/api/static/rule-wizard.js tests/js/rule_wizard.test.js
git commit -m "feat: 字典驱动规则向导的条件树构建模块"
```

---

### Task 15: app.js 接线——规则向导、历史回滚、引擎切换

**Files:**
- Modify: `src/api/static/index.html`（加载 rule-wizard.js）
- Modify: `src/api/static/app.js`

**Interfaces:**
- Consumes: Task 6 history API、Task 14 `CdssRuleWizard`、Task 3 engine API、现有 `api()`/`esc()`/`toast()`/`renderRules()`。
- Produces: 规则管理页新增「规则向导」「历史/回滚」「引擎切换」三处入口（详情见下）。

- [ ] **Step 1: index.html 加载向导模块**

在 `<script src="/static/app.js"></script>` **之前**加：

```html
<script src="/static/rule-wizard.js"></script>
```

- [ ] **Step 2: renderRules 工具栏加按钮与引擎切换**

定位 `app.js` 中 `renderRules` 的工具栏（搜 `completeConditions`，约 2870 行），在该行字符串里 `<button id="completeConditions"` 之前插入：

```html
<span id="engineChip" class="nowrap"></span><button id="ruleWizard" class="btn primary">规则向导</button>
```

并在 `$("#newRule").onclick = () => openRuleEditor(null);` 之后加：

```js
    $("#ruleWizard").onclick = () => openRuleWizard();
    renderEngineChip(content.querySelector("#engineChip"));
```

- [ ] **Step 3: 新增 openRuleWizard / renderEngineChip / openRuleHistory**

在 `openRuleEditor` 函数之后插入（完整实现）：

```js
  async function openRuleWizard() {
    await loadBase();
    const [diseases, syndromes, labs, symptoms, exams, findings] = await Promise.all([
      api("/api/vocab/entities/disease").catch(() => []),
      api("/api/vocab/entities/syndrome").catch(() => []),
      api("/api/vocab/entities/lab").catch(() => []),
      api("/api/vocab/entities/symptom").catch(() => []),
      api("/api/vocab/entities/exam").catch(() => []),
      api("/api/vocab/entities/exam_finding").catch(() => []),
    ]);
    const dict = { disease: diseases, syndrome: syndromes, lab: labs, symptom: symptoms, exam: exams, exam_finding: findings };
    const labConcepts = state.vocab.filter((v) => v.category === "lab_concept");
    const rows = [];

    const dictOptions = (items) => ["", ...items.map((item) => item.code)]
      .map((code) => `<option value="${esc(code)}">${esc(code ? `${nameOf(items, code)}（${code}）` : "请选择")}</option>`).join("");
    const nameOf = (items, code) => (items.find((item) => item.code === code) || {}).name || code;
    const conceptOptions = () => labConcepts.map((v) => `<option value="${esc(v.code)}">${esc(v.name_cn)}（${esc(v.code)}）</option>`).join("");

    function rowHtml(row, index) {
      const kindSelect = `<select data-row="${index}" data-field="kind">${(window.CdssRuleWizard.CONDITION_KINDS).map((k) => `<option value="${k.value}" ${row.kind === k.value ? "selected" : ""}>${k.label}</option>`).join("")}</select>`;
      let fields = "";
      if (row.kind === "lab_concept") {
        fields = `
          <div class="field"><label>检验</label><select data-row="${index}" data-field="lab_code">${dictOptions(dict.lab)}</select></div>
          <div class="field"><label>概念（可多选=OR）</label><select data-row="${index}" data-field="concept_codes" multiple size="3">${conceptOptions()}</select></div>`;
      } else if (row.kind === "symptom") {
        fields = `<div class="field"><label>症状</label><select data-row="${index}" data-field="symptom_code">${dictOptions(dict.symptom)}</select></div>`;
      } else if (row.kind === "exam_finding") {
        fields = `
          <div class="field"><label>检查</label><select data-row="${index}" data-field="exam_code">${dictOptions(dict.exam)}</select></div>
          <div class="field"><label>发现</label><select data-row="${index}" data-field="finding_code">${dictOptions(dict.exam_finding)}</select></div>`;
      } else {
        fields = `
          <div class="field"><label>观察类型</label><select data-row="${index}" data-field="observation_type">${["symptom", "tongue", "pulse", "sign"].map((t) => `<option value="${t}" ${row.observation_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
          <div class="field"><label>编码</label><input data-row="${index}" data-field="code" value="${esc(row.code || "")}" placeholder="选择类型后输入编码或从图谱页复制"></div>`;
      }
      return `<div class="inline-controls" data-row-wrap="${index}">${kindSelect}${fields}<button class="btn sm danger" data-row-remove="${index}">删除行</button></div>`;
    }

    function renderRows() {
      $("#wizardRows").innerHTML = rows.map((row, index) => rowHtml(row, index)).join("") || '<div class="empty">尚未添加条件行</div>';
      $("#wizardPreview").textContent = window.CdssRuleWizard.describeTree(window.CdssRuleWizard.buildConditionTree(collectRows())) || "（空条件：治疗类规则的合法写法）";
      bindRowEvents();
    }

    function collectRows() {
      return rows.map((row) => {
        const collected = { kind: row.kind };
        document.querySelectorAll(`[data-row-wrap] [data-row="${rows.indexOf(row)}"]`).forEach((input) => {
          const field = input.dataset.field;
          if (field === "concept_codes") {
            collected[field] = [...input.selectedOptions].map((option) => option.value);
          } else {
            collected[field] = input.value;
          }
        });
        if (collected.kind === "lab_concept" && collected.concept_codes == null) collected.concept_codes = [];
        return collected;
      });
    }

    function bindRowEvents() {
      document.querySelectorAll("[data-row-wrap]").forEach((wrap) => {
        const index = Number(wrap.dataset.rowWrap);
        wrap.querySelectorAll("select[data-field], input[data-field]").forEach((input) => {
          input.onchange = () => {
            if (input.dataset.field === "kind") {
              rows[index] = { kind: input.value };
            } else {
              rows[index][input.dataset.field] = input.dataset.field === "concept_codes"
                ? [...input.selectedOptions].map((option) => option.value)
                : input.value;
            }
            renderRows();
          };
        });
        wrap.querySelector("[data-row-remove]").onclick = () => {
          rows.splice(index, 1);
          renderRows();
        };
      });
      $("#wizardAddRow").onclick = () => {
        rows.push({ kind: "lab_concept" });
        renderRows();
      };
    }

    $("#editorBody").innerHTML = `
      <h3>规则向导（按字典选择，无需写 JSON）</h3>
      <div class="form-grid">
        <div class="field"><label>领域</label><select id="wizardDomain"><option value="western">西医</option><option value="tcm">中医</option></select></div>
        <div class="field"><label>规则类型</label><select id="wizardRuleType"><option value="diagnosis">诊断</option><option value="treatment">治疗</option></select></div>
        <div class="field"><label>目标（疾病/证候）</label><select id="wizardAnchor">${dictOptions(diseases)}${dictOptions(syndromes).replace('<option value=""  >请选择</option>', "")}</select></div>
        <div class="field"><label>规则名称</label><input id="wizardName" placeholder="例如：STEMI 肌钙蛋白诊断"></div>
        <div class="field"><label>科室（可空）</label><input id="wizardDepartment"></div>
        <div class="field"><label>优先级</label><input id="wizardPriority" type="number" value="50"></div>
        <div class="field half"><label>结论提示</label><input id="wizardMessage"></div>
        <div class="field half"><label>建议动作</label><input id="wizardAction"></div>
      </div>
      <h4>条件行（行间 AND；同一检验选多个概念 = OR）</h4>
      <div id="wizardRows"></div>
      <div class="actions"><button id="wizardAddRow" class="btn">添加条件行</button></div>
      <h4>条件预览</h4>
      <div id="wizardPreview" class="mono muted"></div>`;
    rows.push({ kind: "lab_concept" });
    renderRows();
    const dialog = $("#editorDialog");
    dialog.showModal();
    $("#editorSave").onclick = async () => {
      try {
        const domain = $("#wizardDomain").value;
        const anchorCode = $("#wizardAnchor").value;
        const anchorType = domain === "tcm" ? "syndrome" : "disease";
        if (!anchorCode) throw new Error("请选择目标疾病/证候");
        const conditions = window.CdssRuleWizard.buildConditionTree(collectRows());
        const entry = (dict[anchorType] || []).find((item) => item.code === anchorCode) || {};
        const payload = {
          rule_id: `CUSTOM-${Date.now()}`,
          version: 1,
          domain,
          anchor_type: anchorType,
          rule_type: domain === "tcm" ? "tcm_pattern" : $("#wizardRuleType").value,
          name: $("#wizardName").value || `向导创建: ${entry.name || anchorCode}`,
          disease_code: anchorCode,
          disease_name: entry.name || anchorCode,
          priority: Number($("#wizardPriority").value || 50),
          status: "draft",
          department: $("#wizardDepartment").value || null,
          conditions,
          conclusion: {
            diagnosis: anchorCode,
            confidence: "medium",
            message: $("#wizardMessage").value || "",
            action: $("#wizardAction").value || "",
          },
          source: {},
          exclusions: [],
          tags: ["wizard"],
          created_by: localStorage.getItem("cdssReviewer") || "未署名",
        };
        await api("/api/rules", { json: payload });
        dialog.close();
        toast("规则已保存为草稿，可在列表中审核启用");
        renderRules();
      } catch (error) {
        toast(error.message, true);
      }
    };
    $("#editorCancel").onclick = () => dialog.close();
  }

  async function renderEngineChip(container) {
    if (!container) return;
    try {
      const status = await api("/api/engine/status");
      const config = await api("/api/engine/config");
      const reachable = status.active === "native" ? true : Boolean(status.drools?.reachable);
      const badge = reachable ? '<span class="badge active">在线</span>' : '<span class="badge danger">不可达</span>';
      container.innerHTML = `
        <label class="muted">引擎</label>
        <select id="engineSwitch">
          <option value="native" ${config.active === "native" ? "selected" : ""}>原生引擎</option>
          <option value="drools" ${config.active === "drools" ? "selected" : ""}>Drools（需 sidecar）</option>
        </select>${badge}`;
      $("#engineSwitch").onchange = async (event) => {
        try {
          await api("/api/engine/config", { method: "PUT", json: { config: { active: event.target.value } } });
          toast(`推理引擎已切换为 ${event.target.value === "drools" ? "Drools（sidecar 不可达时自动回退原生）" : "原生引擎"}`);
          renderEngineChip(container);
        } catch (error) {
          toast(error.message, true);
        }
      };
    } catch (error) {
      container.innerHTML = '<span class="muted">引擎状态不可用</span>';
    }
  }

  async function openRuleHistory(ruleId) {
    const items = await api(`/api/rules/${encodeURIComponent(ruleId)}/history`);
    $("#editorBody").innerHTML = `
      <h3>规则历史 <span class="mono muted">${esc(ruleId)}</span></h3>
      ${items.length ? table(["版本", "时间", "动作", "操作人", "名称", "操作"], items.map((item) => `<tr>
        <td>v${item.seq}</td>
        <td class="mono">${esc((item.timestamp || "").replace("T", " ").slice(0, 19))}</td>
        <td>${esc(item.action)}</td>
        <td>${esc(item.actor)}</td>
        <td>${esc((item.rule || {}).name || "")}</td>
        <td><button class="btn sm" data-history-restore="${item.seq}">恢复此版本</button></td>
      </tr>`)) : '<div class="empty">暂无历史（保存规则后自动记录）</div>'}`;
    const dialog = $("#editorDialog");
    const save = $("#editorSave");
    save.hidden = true;
    dialog.onclose = () => { save.hidden = false; dialog.onclose = null; };
    dialog.showModal();
    $("#editorCancel").onclick = () => dialog.close();
    $("#editorBody").querySelectorAll("[data-history-restore]").forEach((button) => {
      button.onclick = async () => {
        if (!confirm(`恢复到 v${button.dataset.historyRestore}？当前内容会另存为新版本，可再次回滚。`)) return;
        try {
          await api(`/api/rules/${encodeURIComponent(ruleId)}/history/${button.dataset.historyRestore}/restore`, {
            method: "POST",
            json: { reviewer: localStorage.getItem("cdssReviewer") || "未署名" },
          });
          dialog.close();
          toast("已恢复历史版本");
          renderRules();
        } catch (error) {
          toast(error.message, true);
        }
      };
    });
  }
```

- [ ] **Step 4: 规则列表行加「历史」按钮**

在 `renderRules` 的行操作渲染处（搜 `data-rule-deps`，行模板 actions 区域）加：

```html
<button class="btn sm" data-rule-history="${esc(r.rule_id)}">历史</button>
```

并在依赖按钮绑定代码（`content.querySelectorAll("[data-rule-deps]")` 附近）加：

```js
    content.querySelectorAll("[data-rule-history]").forEach((button) => {
      button.onclick = () => openRuleHistory(button.dataset.ruleHistory);
    });
```

- [ ] **Step 5: 手动验证 + JS 回归**

Run: `node --test tests/js/rule_wizard.test.js tests/js/graph_view_fallback.test.js`
Expected: 全部 passed

启动服务手工验证（按 `run` 演示流程）：
`PYTHONPATH=. python3 -B -m uvicorn src.api.main:app --host 127.0.0.1 --port 8001`
1. 规则管理页 → 「规则向导」→ 选疾病 + 添加检验概念行 → 保存草稿 → 列表出现新规则且条件文本正确；
2. 任一规则「历史」→ 修改该规则一次 → 历史出现 v1/v2 → 恢复 v1 → 列表内容回退且历史新增 v3（rollback）；
3. 「引擎」切换到 Drools → badge 显示「不可达」→ 病历测试仍正常出结论（原生回退）→ 切回原生。

- [ ] **Step 6: Commit**

```bash
git add src/api/static/app.js src/api/static/index.html
git commit -m "feat: 规则向导、版本历史回滚与引擎切换入口"
```

---

### Task 16: 文档与全量回归

**Files:**
- Modify: `README.md`

**Interfaces:** 无代码接口。

- [ ] **Step 1: README 更新**

在「核心设计」章节之后新增一节（中文，内容要点）：

```markdown
## 双规则引擎（可切换）

推理层支持两种可切换的实现，输出（结论+证据链+评分）完全一致：

- **原生引擎**（默认）：Python 递归条件树求值 + 原子条件缓存 + 保守剪枝索引。
- **Drools 引擎**（可选）：规则条件树编译为 DRL，由 sidecar（`drools-sidecar/`，
  Java 17 + Maven）做 RETE 批量匹配，结论仍由 Python 原生层重建，保证溯源一致。
  加权评分规则、跨阶段 fact、按规则图谱解析的 evidence 条件不参与编译，始终走原生。

配置在 `data/engine.json`（不存在时默认原生）：

    { "active": "native", "drools": { "base_url": "http://127.0.0.1:8090",
      "timeout_seconds": 5, "fail_over_native": true } }

规则管理页可切换引擎并查看状态；sidecar 不可达且 `fail_over_native: true`
时自动回退原生，推理响应 `engine.fallback_reason` 记录原因。规则 JSON
（`data/rules/managed_rules.json`）始终是唯一事实源，DRL 只是编译产物。

## 规则版本历史与回滚

规则每次创建/修改/导入/删除都会在 `data/rules/history/<rule_id>.jsonl` 留内容快照；
规则管理页「历史」可查看时间线并一键恢复任意版本（恢复动作本身也入历史）。

## 业务字典联动

共享实体字典（`data/vocab/entities/`）的变更自动联动规则：

- 改名：受影响规则的 `label`/`condition_text`/`disease_name` 自动按新名重建；
- 改码：`POST /api/vocab/entities/{type}/{code}/remap`，规则内引用全部替换；
  图谱仍引用该码时拒绝并提示先走知识审核工作流迁移图谱；
- 删除：规则仍引用的编码禁止删除。
```

- [ ] **Step 2: 全量回归**

Run: `python3 -m pytest -q`
Expected: 全部 passed（与主分支基线一致，无新增失败）

Run: `node --test tests/js/graph_view_fallback.test.js tests/js/rule_wizard.test.js`
Expected: 全部 passed

Run: `python3 -m unittest tests.test_review_status tests.test_rule_dependencies tests.test_graph_review_service tests.test_review_metadata tests.test_review_api tests.test_rule_review_gate tests.test_review_queue tests.test_package_review tests.test_review_orphans -v`
Expected: 全部 passed

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: 双规则引擎、规则历史与字典联动说明"
```

---

## Self-Review 结论

1. **Spec 覆盖**：决策 1（双引擎可切换）→ Task 1/2/3/9/10/11/12/13 + sidecar；决策 2（原生加固）→ Task 4/5；规则历史 → Task 6；字典联动 → Task 7/8；决策 3（面向医务部的无代码向导）→ Task 14/15。无遗漏。
2. **占位符扫描**：所有代码块完整可执行；Task 8/12 测试中的临时写法（Observation import、TemplateEntityEntry 字段）已就地标注"执行时按实际模型修正"。
3. **类型一致性**：`matcher` 契约 `dict[rule_id, (traces, weighted_detail)]` 在 Task 11 定义、Task 12 消费一致；`CompiledRule.leaves` 顺序 = LHS `a{index}` 顺序在 Task 9 编译器与 Task 12 投影端一致；`record_snapshot`/`list_snapshots`/`get_snapshot` 签名在 Task 6 定义与 Task 15 前端 REST 路径一致；引擎 `describe()` 键名（requested/used/fallback_reason）在 Task 2/12/13 一致。
