"""Read-only reference catalogues used to populate active entity dictionaries.

The first catalogue is a trial ICD-10 subset.  It intentionally does not enter
guideline extraction directly: extraction still reads the small active disease
dictionary, while this catalogue provides curated standard codes for search and
explicit import.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent.parent
REFERENCE_VOCAB_DIR = ROOT / "data" / "vocab" / "reference"
REFERENCE_BUNDLE_PATH = REFERENCE_VOCAB_DIR / "icd10_bundles.json"
ICD10_CODE_PATTERN = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")

CATALOG_CODE_FIELDS = ("code", "疾病编码", "诊断编码", "ICD-10编码", "icd10_code")
CATALOG_NAME_FIELDS = ("name", "疾病名称", "诊断名称", "标准名称", "name_cn")
CATALOG_ALIAS_FIELDS = ("aliases", "别名", "同义词", "synonyms")


class ReferenceVocabularyRepository:
    """Loader and keyword search for a JSON reference catalogue."""

    def __init__(self, standard: str = "icd10", directory: Path = REFERENCE_VOCAB_DIR):
        self.standard = standard
        self.directory = directory
        self.path = directory / f"{standard}.json"

    def load(self) -> Dict[str, Any]:
        """Read the catalogue and fail clearly if its file is absent."""
        if not self.path.exists():
            raise FileNotFoundError(f"参考库不存在: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError(f"参考库文件结构无效: {self.path}")
        return data

    def metadata(self) -> Dict[str, Any]:
        data = self.load()
        return {key: value for key, value in data.items() if key != "entries"}

    def entries(self) -> List[Dict[str, Any]]:
        return [item for item in self.load().get("entries", []) if isinstance(item, dict)]

    def find(self, code: str) -> Optional[Dict[str, Any]]:
        wanted = str(code or "").strip().upper()
        return next((item for item in self.entries() if str(item.get("code", "")).upper() == wanted), None)

    def search(self, query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Search by ICD-10 code prefix, standard name or alias containment."""
        keyword = str(query or "").strip()
        if not keyword:
            return self.entries()[: max(0, limit)]
        lowered = keyword.lower()
        matched: List[Dict[str, Any]] = []
        for item in self.entries():
            code = str(item.get("code", ""))
            names = [str(item.get("name", ""))] + [str(alias) for alias in item.get("aliases", [])]
            if code.lower().startswith(lowered) or any(keyword in name for name in names):
                matched.append(item)
            if len(matched) >= max(0, limit):
                break
        return matched

    def write(self, metadata: Dict[str, Any], entries: List[Dict[str, Any]]) -> None:
        """Replace the local catalogue after an explicit validated import."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**metadata, "entries": entries}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ReferenceBundleRepository:
    """Specialty-oriented activation bundles over a reference catalogue."""

    def __init__(self, path: Path = REFERENCE_BUNDLE_PATH):
        self.path = Path(path)

    def list(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"专科包文件结构无效: {self.path}")
        return [item for item in data if isinstance(item, dict)]

    def get(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self.list() if item.get("bundle_id") == bundle_id), None)

    @staticmethod
    def entries(bundle: Dict[str, Any], catalogue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        prefixes = tuple(str(value).upper() for value in bundle.get("code_prefixes") or [])
        explicit_codes = {str(value).upper() for value in bundle.get("codes") or []}
        return [
            item for item in catalogue
            if str(item.get("code") or "").upper() in explicit_codes
            or (prefixes and str(item.get("code") or "").upper().startswith(prefixes))
        ]


def _first(raw: Dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = raw.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _aliases(raw: Dict[str, Any]) -> List[str]:
    value: Any = next((raw.get(field) for field in CATALOG_ALIAS_FIELDS if raw.get(field) is not None), [])
    if isinstance(value, list):
        aliases = value
    else:
        aliases = re.split(r"[|;；、\n]+", str(value or ""))
    return sorted({str(alias).strip() for alias in aliases if str(alias).strip()})


def parse_reference_catalog(content: bytes, filename: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse an authorized JSON or CSV disease catalogue upload."""
    suffix = Path(filename or "").suffix.lower()
    text = content.decode("utf-8-sig")
    if suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        return {}, [dict(row) for row in rows]
    if suffix == ".json":
        payload = json.loads(text)
        if isinstance(payload, list):
            return {}, [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            metadata = {key: value for key, value in payload.items() if key != "entries"}
            return metadata, [item for item in payload["entries"] if isinstance(item, dict)]
    raise ValueError("仅支持 JSON 或 UTF-8 CSV，且文件必须包含疾病条目列表")


def validate_reference_entries(raw_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize imported rows and return a non-writing validation report."""
    normalized: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    seen: set[str] = set()
    if len(raw_entries) > 100_000:
        return {
            "valid": False,
            "entries": [],
            "errors": [{"row": 0, "message": "单次导入最多 100000 条"}],
            "warnings": [],
        }
    for index, raw in enumerate(raw_entries, start=1):
        code = _first(raw, CATALOG_CODE_FIELDS).upper()
        name = _first(raw, CATALOG_NAME_FIELDS)
        if not code or not ICD10_CODE_PATTERN.fullmatch(code):
            errors.append({"row": index, "code": code, "message": "疾病编码不是有效 ICD-10 格式"})
            continue
        if not name:
            errors.append({"row": index, "code": code, "message": "疾病标准名称为空"})
            continue
        if code in seen:
            errors.append({"row": index, "code": code, "message": "文件内疾病编码重复"})
            continue
        seen.add(code)
        aliases = [alias for alias in _aliases(raw) if alias != name]
        if not aliases:
            warnings.append({"row": index, "code": code, "message": "未提供别名"})
        normalized.append({
            "code": code,
            "name": name,
            "aliases": aliases,
            "domain": "western",
            "standard_system": "ICD-10",
            "standard_code": code,
            "source": str(raw.get("source") or "ICD-10"),
            "specialty": str(raw.get("specialty") or raw.get("专科") or "").strip(),
        })
    return {
        "valid": not errors,
        "entries": normalized,
        "errors": errors,
        "warnings": warnings,
    }


def import_reference_catalog(
    repository: ReferenceVocabularyRepository,
    raw_entries: List[Dict[str, Any]],
    *,
    metadata: Optional[Dict[str, Any]] = None,
    version: str = "",
    source: str = "",
    mode: str = "merge",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Validate and optionally merge/replace a reference catalogue."""
    if mode not in {"merge", "replace"}:
        raise ValueError("导入模式必须是 merge 或 replace")
    report = validate_reference_entries(raw_entries)
    current = repository.load()
    current_entries = [item for item in current.get("entries", []) if isinstance(item, dict)]
    incoming = report.pop("entries")
    existing_codes = {str(item.get("code") or "").upper() for item in current_entries}
    incoming_codes = {item["code"] for item in incoming}
    added = len(incoming_codes - existing_codes)
    updated = len(incoming_codes & existing_codes)
    unchanged = len(existing_codes - incoming_codes) if mode == "replace" else len(existing_codes)
    result = {
        **report,
        "mode": mode,
        "dry_run": dry_run,
        "incoming_count": len(incoming),
        "existing_count": len(current_entries),
        "added_count": added,
        "updated_count": updated,
        "retained_count": unchanged,
    }
    if not report["valid"] or dry_run:
        return result

    if mode == "replace":
        merged = incoming
    else:
        by_code = {str(item.get("code") or "").upper(): item for item in current_entries}
        by_code.update({item["code"]: item for item in incoming})
        merged = list(by_code.values())
    merged.sort(key=lambda item: str(item.get("code") or ""))
    existing_metadata = {key: value for key, value in current.items() if key != "entries"}
    imported_metadata = dict(metadata or {})
    final_metadata = {
        **existing_metadata,
        **imported_metadata,
        "standard": "ICD-10",
        "version": version or imported_metadata.get("version") or existing_metadata.get("version") or "",
        "source": source or imported_metadata.get("source") or existing_metadata.get("source") or "",
        "imported_at": date.today().isoformat(),
        "scope": "用于搜索选用，不直接参与指南解析识别",
    }
    repository.write(final_metadata, merged)
    result["result_count"] = len(merged)
    return result
