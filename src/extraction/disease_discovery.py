"""Reusable disease-name discovery for any guideline template."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import TemplateEntityType
from .concept_mapping import GENERIC_DISEASE_BLOCKLIST


# Common medical endings keep automatic discovery conservative. A vocabulary
# match always takes priority over a new automatic name.
_DISEASE_SUFFIXES = [
    "心肌梗死", "脑梗死", "淋巴瘤", "白血病", "骨髓瘤", "综合征", "结节病",
    "高脂血症", "心肌病", "心肌炎", "心包炎", "心内膜炎", "瓣膜病", "功能障碍",
    "营养不良", "脓毒症", "糖尿病", "肝硬化", "慢阻肺", "肾衰竭", "肝衰竭",
    "心力衰竭", "呼吸衰竭", "肾损伤", "肝损伤", "高血压", "贫血", "肿瘤",
    "肉瘤", "结核", "肝炎", "肺炎", "肾炎", "梗死", "栓塞",
    "卒中", "休克", "感染", "结石", "溃疡", "损伤", "骨折", "哮喘",
    "癌症", "癌", "瘤",
]
# Common verb/particle preceders legitimately introduce a disease name inside
# a Chinese sentence. Without them the strict word-boundary lookbehind
# misses almost every mid-sentence mention.
_DISEASE_PRECEDERS = (
    "确诊", "诊断", "考虑", "提示", "疑诊", "拟诊", "患有", "罹患", "罹",
    "并发", "合并", "伴", "继发", "见于", "因为", "由于", "除外", "排除",
    "包括", "例如", "为", "是", "及", "和", "与", "或", "的", "该", "此",
)
_DISEASE_BOUNDARY = (
    r"(?:(?<![\w\u4e00-\u9fa5])"
    + "".join("|(?<=" + p + ")" for p in _DISEASE_PRECEDERS)
    + r")"
)
_DISEASE_PATTERN = re.compile(
    _DISEASE_BOUNDARY +
    r"(?P<name>[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9／(/)-]{0,18}?)"
    r"(?P<suffix>" + "|".join(sorted(set(_DISEASE_SUFFIXES), key=len, reverse=True)) + r")",
    re.IGNORECASE,
)
# Leading context words usually mean the captured span is sentence text, not
# a standalone disease name. These are checked after trimming and never block
# medical compounds such as 非霍奇金淋巴瘤 or 病毒性肺炎.
_DISEASE_CONTEXT_PREFIXES = (
    "如", "例如", "包括", "对于", "关于", "针对", "用于", "应用", "可用于",
    "可应用", "决定", "还可", "尽量", "唯一", "有助于", "否具有", "可以参",
    "些特定", "时可", "判断", "发现", "任何原发", "CT", "均参", "放疗在",
    "不再对", "除感染", "无感染", "其白血", "中国", "欧洲", "德国", "国际",
    "国立", "流式", "基因突变检测", "GELF", "应采取", "目前", "本", "该", "此",
    "目前", "本", "该", "此", "一种", "一组", "所有", "大多数", "其他",
    "不同", "介于", "参照", "按照", "显示", "提示", "预防", "避免", "导致",
    "出现", "具有", "既往", "进行", "约占", "如果", "若", "而", "且", "但",
    "在", "从", "于", "由", "因", "对", "以", "者", "胞", "巴", "奇", "性",
    "制定", "常见",
)
_DISEASE_STOPWORDS = {
    "病毒", "病房", "病历", "病例", "病灶", "病因", "病理", "病变", "病史",
    "病机", "病人", "病症", "病程", "病床", "治病", "看病", "疾病史",
    "诊断", "治疗", "分期", "评估", "目录", "附录", "指南", "推荐", "证据",
}
_DISEASE_PREFIX_PATTERN = re.compile(
    r"^(?:鉴别诊断|诊断和?治疗|诊断|治疗|分期|评估|目录|附录|指南|第[一二三四五六七八九十0-9]+[章节部分])\s*"
)
# Characters that turn a leading structural word into a real compound name,
# e.g. 病毒 + 性 -> 病毒性肺炎. A bare structural word remains invalid.
_DISEASE_COMPOUND_CONNECTIVES = {"性", "状", "体", "原", "因", "毒"}
_DISEASE_VOCAB: List[Dict[str, Any]] | None = None
_SHARED_VOCAB_CACHE: Dict[str, List[Dict[str, Any]]] = {}

_SYNDROME_SUFFIXES = ["证候", "证", "型"]
_SYNDROME_STOPWORDS = {
    "辨证", "证候", "主证", "兼证", "变证", "证型", "分型", "辨证论治",
    "中医辨证", "辨证分型", "临床证候", "适应证", "禁忌证", "并发症",
    "症状", "体征", "证明", "验证", "保证", "认证", "考证", "论证",
}
_SYNDROME_PATTERN = re.compile(
    _DISEASE_BOUNDARY +
    r"(?P<name>[一-龥A-Za-z][一-龥A-Za-z0-9／(/)-]{0,18}?)"
    r"(?P<suffix>" + "|".join(sorted(_SYNDROME_SUFFIXES, key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


def _compiled_alias(alias: str):
    try:
        return re.compile(alias, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(alias), re.IGNORECASE)


def _match_quote(text: str, start: int, end: int) -> str:
    left = max(0, start - 45)
    right = min(len(text), end + 85)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _load_shared_entity_vocab(entity_type: str) -> List[Dict[str, Any]]:
    """Load one shared entity dictionary (syndrome, tongue, ...)."""
    if entity_type in _SHARED_VOCAB_CACHE:
        return _SHARED_VOCAB_CACHE[entity_type]
    path = (
        Path(__file__).resolve().parents[2]
        / "data" / "vocab" / "entities" / f"{entity_type}.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = [item for item in payload if item.get("code") and item.get("name")]
    except (OSError, json.JSONDecodeError):
        items = []
    _SHARED_VOCAB_CACHE[entity_type] = items
    return items


def _vocab_code_for_alias_in(vocab: List[Dict[str, Any]], name: str) -> str:
    normalized = name.casefold()
    for item in vocab:
        names = [str(item.get("name") or ""), *map(str, item.get("aliases") or [])]
        if normalized in {value.strip().casefold() for value in names if value.strip()}:
            return str(item["code"])
    return ""


def _load_disease_vocab() -> List[Dict[str, Any]]:
    """Load ICD-10 western diseases from the shared entity dictionary.

    ``disease.json`` is now a pure ICD-10 western dictionary. TCM diseases are
    template-local entries and are intentionally not loaded from it. The
    legacy ``diseases.json`` remains only as a backward-compatible fallback.
    """
    global _DISEASE_VOCAB
    if _DISEASE_VOCAB is not None:
        return _DISEASE_VOCAB
    shared_path = (
        Path(__file__).resolve().parents[2]
        / "data" / "vocab" / "entities" / "disease.json"
    )
    payload: List[Dict[str, Any]] = []
    if shared_path.exists():
        try:
            raw = json.loads(shared_path.read_text(encoding="utf-8"))
            payload = [
                item for item in raw
                if item.get("code") and item.get("name")
                and str(item.get("domain") or "western").lower() == "western"
                and str(item.get("standard_system") or "ICD-10").upper().startswith("ICD-10")
            ]
        except (OSError, json.JSONDecodeError):
            payload = []
    if not payload:
        legacy_path = Path(__file__).resolve().parents[2] / "data" / "vocab" / "diseases.json"
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
            payload = [item for item in raw if item.get("code") and item.get("name")]
        except (OSError, json.JSONDecodeError):
            payload = []
    _DISEASE_VOCAB = payload
    return _DISEASE_VOCAB


def _normalize_discovered_name(value: str) -> str:
    value = re.sub(r"^[\[（(【]\s*", "", value).strip()
    value = re.sub(r"\s*[\]）)】]$", "", value).strip()
    value = _DISEASE_PREFIX_PATTERN.sub("", value).strip()
    return value.strip(" 　:：、，。;；/／-—()（）【】[]")


def _trim_leading_preceders(name: str) -> str:
    """Cut document-context text the regex swallowed before the real name.

    Leftmost regex matches can start at the sentence head and include words
    like 常见病因为 / 病理确诊的. Cut at the last preceder so the real
    disease part remains, e.g. 常见病因为病毒性 -> 病毒性.
    """
    cut = -1
    for word in _DISEASE_PRECEDERS:
        idx = name.rfind(word)
        if idx >= 0:
            cut = max(cut, idx + len(word))
    return name[cut:] if cut >= 0 else name


# Structural fragments inside a captured name mean it is document text,
# not a disease (e.g. 典型的临床表现肺炎). Checked anywhere in the name.
_DISEASE_NAME_NOISE = (
    "临床表现", "症状", "体征", "患者", "治疗", "方案", "检查", "分期",
    "评估", "风险", "随访", "诊断", "病史", "病程", "病死", "水平", "确诊",
    "常见", "版", "版本", "指南", "共识", "国立", "大学", "医院", "学会",
    "协会", "协作", "研究", "决定", "可应用", "应用于", "有助于", "评价",
    "检测", "测定", "参照", "避免", "预防", "措施", "治愈", "选择", "尽量",
    "唯一", "判断", "发现", "显示", "残存", "放疗在", "化疗在", "不再",
    "中国", "欧洲", "德国", "国际", "流式细胞术", "基因突变检测", "任何原发",
)
# A very short generic tumor stem is usually document noise (高肿瘤/低肿瘤).
# Common anatomical one-character roots remain valid for compact disease names.
_DISEASE_ONE_CHAR_TUMOR_ROOTS = {
    "肺", "胃", "肝", "肾", "脑", "骨", "眼", "耳", "鼻", "喉", "乳",
    "皮", "血", "胆", "脾", "心",
}

# These checks apply only to newly discovered candidate names. A curated
# vocabulary hit is authoritative and keeps its canonical spelling.
_DISEASE_STAGE_PREFIX_PATTERN = re.compile(
    r"^(?:[IVXivxⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|[一二三四五六七八九十百]+|\d+)"
    r"(?:\s*[\/／~～\-—至]\s*[IVXivxⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)?\s*期"
)
_DISEASE_ORPHAN_PREFIXES = (
    "细胞淋巴瘤", "细胞白血病", "细胞肿瘤", "细胞肉瘤", "血细胞综合征",
    "原发肿瘤",
)
_DISEASE_EVALUATION_PREFIXES = (
    "简易", "评分", "指数", "量表",
)
_DISEASE_CONTEXT_MARKERS = (
    "评分", "指数", "分期", "方案", "目录", "将",
    "通过", "发挥", "等部位",
)
# Common cell/agent markers can introduce a compact disease name. Unknown
# uppercase strings usually come from OCR, tables or scoring-system names.
_DISEASE_ALLOWED_LATIN_PREFIXES = {
    "T", "B", "NK", "NKT", "EBV", "HIV", "MALT", "DLBCL", "ALK", "CNS", "WHO",
}
_DISEASE_CELL_MARKER_PREFIX_PATTERN = re.compile(r"^CD\d+$")
_DISEASE_LATIN_PREFIX_PATTERN = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)")


def _vocab_code_for_alias(name: str) -> str:
    """Return the canonical vocabulary code for an exact disease alias."""
    normalized = name.casefold()
    for item in _load_disease_vocab():
        names = [str(item.get("name") or ""), *map(str, item.get("aliases") or [])]
        if normalized in {value.strip().casefold() for value in names if value.strip()}:
            return str(item["code"])
    return ""


def _valid_discovered_name(name: str) -> bool:
    if not name or len(name) < 2:
        return False
    if any(frag in name for frag in _DISEASE_NAME_NOISE):
        return False
    if any(name.startswith(prefix) for prefix in _DISEASE_CONTEXT_PREFIXES):
        return False
    # Generic tumor words need enough specificity. Compact anatomical cancer
    # names (肺癌/肝癌) are still allowed through the curated root list.
    if name.endswith("肿瘤"):
        root = name[:-2]
        if len(root) < 2 and root not in _DISEASE_ONE_CHAR_TUMOR_ROOTS:
            return False
    for word in _DISEASE_STOPWORDS:
        if name == word:
            return False
        # Only reject when a structural word LEADS the name. Real medical
        # compounds such as 病毒性肺炎 / 病毒性心肌炎 start with 病毒 but
        # continue with a compound-forming character and must survive.
        if name.startswith(word):
            rest = name[len(word):]
            if rest and rest[0] in _DISEASE_COMPOUND_CONNECTIVES:
                return True
            return False
    return True


def _valid_auto_discovered_name(name: str) -> bool:
    """Apply conservative checks to a candidate before medical review."""
    if not _valid_discovered_name(name):
        return False
    if name in GENERIC_DISEASE_BLOCKLIST:
        return False
    if _DISEASE_STAGE_PREFIX_PATTERN.match(name):
        return False
    if any(name == prefix or name.startswith(prefix) for prefix in _DISEASE_ORPHAN_PREFIXES):
        return False
    if any(name.startswith(prefix) for prefix in _DISEASE_EVALUATION_PREFIXES):
        return False
    if any(marker in name for marker in _DISEASE_CONTEXT_MARKERS):
        return False
    # Unknown all-uppercase strings anywhere in a candidate usually come from
    # OCR, treatment tables or scoring-system names. Keep known clinical
    # markers and mixed-case medical words, reject the rest.
    for found in re.finditer(r"[A-Za-z][A-Za-z0-9-]*", name):
        token = found.group(0)
        if token.upper() in _DISEASE_ALLOWED_LATIN_PREFIXES:
            continue
        if _DISEASE_CELL_MARKER_PREFIX_PATTERN.match(token.upper()):
            continue
        if token[0].isupper() and any(ch.islower() for ch in token):
            continue
        return False
    return True

def _position_for_match(
    start: int,
    positions: List[Tuple[int, int, str]],
    pages: List[Any],
    lines: List[Any],
    chunk: Dict[str, Any],
) -> Tuple[int, int]:
    local_index = next((i for i, (a, b, _) in enumerate(positions) if a <= start < b), 0)
    page = pages[min(local_index, len(pages) - 1)] or chunk.get("page_start", 1)
    line = lines[min(local_index, len(lines) - 1)] or chunk.get("line_start", 1)
    return int(page), int(line)


def _discovered_disease_matches(
    chunk: Dict[str, Any],
    positions: List[Tuple[int, int, str]],
    pages: List[Any],
    lines: List[Any],
    vocab: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Find disease names from vocabulary first, then conservative new names."""
    text = str(chunk.get("text") or "")
    vocab = vocab if vocab is not None else _load_disease_vocab()
    matches: Dict[str, Dict[str, Any]] = {}

    for item in vocab:
        names = [str(item.get("name") or ""), *map(str, item.get("aliases") or [])]
        earliest = None
        for alias in [name for name in names if name]:
            found = _compiled_alias(alias).search(text)
            if found and (earliest is None or found.start() < earliest.start()):
                earliest = found
        if not earliest:
            continue
        name = _normalize_discovered_name(str(item.get("name") or item.get("code")))
        if not _valid_discovered_name(name):
            continue
        key = name.casefold()
        page, line = _position_for_match(earliest.start(), positions, pages, lines, chunk)
        candidate = {
            "name": name,
            "code": str(item["code"]),
            "match": earliest,
            "page": page,
            "line": line,
        }
        if key not in matches or earliest.start() < matches[key]["match"].start():
            matches[key] = candidate

    vocabulary_spans = [
        (item["match"].start(), item["match"].end())
        for item in matches.values()
    ]
    scan_pos = 0
    while scan_pos < len(text):
        found = _DISEASE_PATTERN.search(text, scan_pos)
        if not found:
            break
        # A vocabulary hit is authoritative. Do not create another automatic
        # disease from a suffix-only substring inside the same full name.
        if any(found.start() < end and start < found.end() for start, end in vocabulary_spans):
            scan_pos = found.end()
            continue
        raw_name = _trim_leading_preceders(found.group("name"))
        name = _normalize_discovered_name(raw_name + found.group("suffix"))
        if not _valid_auto_discovered_name(name):
            # Retry one character later: a rejected leftmost match must not
            # hide a shorter valid disease mention inside the same span.
            scan_pos = found.start() + 1
            continue
        # Organization names such as 北京大学肿瘤医院 often end with the same
        # 瘤 character. They are provenance labels, not disease mentions.
        tail = text[found.end():found.end() + 4]
        if re.match(r"\s*(?:医院|学会|学院|大学|研究所|协会|中心|期刊|编辑部)", tail):
            scan_pos = found.end()
            continue
        key = name.casefold()
        if key not in matches:
            canonical_code = _vocab_code_for_alias_in(vocab, name)
            # Exact vocabulary aliases must not become a second AUTO disease
            # even when another spelling from the same concept appeared earlier.
            if canonical_code and any(item.get("code") == canonical_code for item in matches.values()):
                scan_pos = found.end()
                continue
            page, line = _position_for_match(found.start(), positions, pages, lines, chunk)
            code = canonical_code or (
                "AUTO-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8].upper()
            )
            matches[key] = {
                "name": name,
                "code": code,
                "match": found,
                "page": page,
                "line": line,
            }
        scan_pos = found.end()
    return sorted(matches.values(), key=lambda item: item["match"].start())


def _valid_syndrome_name(name: str) -> bool:
    if not name or name in _SYNDROME_STOPWORDS:
        return False
    if len(name) < 3:
        return False
    return _valid_auto_discovered_name(name)


def _discovered_syndrome_matches(
    chunk: Dict[str, Any],
    positions: List[Tuple[int, int, str]],
    pages: List[Any],
    lines: List[Any],
) -> List[Dict[str, Any]]:
    """Discover TCM syndrome names (证候) from vocabulary and conservative text."""
    text = str(chunk.get("text") or "")
    vocab = _load_shared_entity_vocab("syndrome")
    matches: Dict[str, Dict[str, Any]] = {}

    for item in vocab:
        names = [str(item.get("name") or ""), *map(str, item.get("aliases") or [])]
        earliest = None
        for alias in [name for name in names if name]:
            found = _compiled_alias(alias).search(text)
            if found and (earliest is None or found.start() < earliest.start()):
                earliest = found
        if not earliest:
            continue
        name = _normalize_discovered_name(str(item.get("name") or item.get("code")))
        if not _valid_syndrome_name(name):
            continue
        key = name.casefold()
        page, line = _position_for_match(earliest.start(), positions, pages, lines, chunk)
        candidate = {
            "name": name,
            "code": str(item["code"]),
            "match": earliest,
            "page": page,
            "line": line,
        }
        if key not in matches or earliest.start() < matches[key]["match"].start():
            matches[key] = candidate

    vocabulary_spans = [
        (item["match"].start(), item["match"].end())
        for item in matches.values()
    ]
    scan_pos = 0
    while scan_pos < len(text):
        found = _SYNDROME_PATTERN.search(text, scan_pos)
        if not found:
            break
        if any(found.start() < end and start < found.end() for start, end in vocabulary_spans):
            scan_pos = found.end()
            continue
        raw_name = _trim_leading_preceders(found.group("name"))
        name = _normalize_discovered_name(raw_name + found.group("suffix"))
        if not _valid_syndrome_name(name):
            scan_pos = found.start() + 1
            continue
        key = name.casefold()
        if key not in matches:
            canonical_code = _vocab_code_for_alias_in(vocab, name)
            if canonical_code and any(item.get("code") == canonical_code for item in matches.values()):
                scan_pos = found.end()
                continue
            page, line = _position_for_match(found.start(), positions, pages, lines, chunk)
            code = canonical_code or (
                "AUTO-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8].upper()
            )
            matches[key] = {
                "name": name,
                "code": code,
                "match": found,
                "page": page,
                "line": line,
            }
        scan_pos = found.end()
    return sorted(matches.values(), key=lambda item: item["match"].start())


def _discovered_disease_entities(
    chunk: Dict[str, Any],
    entity_type: TemplateEntityType,
    seen: set,
    positions: List[Tuple[int, int, str]],
    pages: List[Any],
    lines: List[Any],
    template_domain: str = "",
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not entity_type.discovery:
        return results
    if entity_type.entity_type == "syndrome":
        discovered_items = _discovered_syndrome_matches(chunk, positions, pages, lines)
    elif entity_type.entity_type == "disease":
        if str(template_domain or "").strip().lower() in {"tcm", "中医", "中医内科"}:
            # TCM disease discovery must map to TCM dictionary codes, never to
            # the ICD-10 western list.
            discovered_items = _discovered_disease_matches(
                chunk, positions, pages, lines,
                vocab=[
                    item for item in _load_shared_entity_vocab("disease")
                    if str(item.get("domain") or "").lower() == "tcm"
                ],
            )
        else:
            discovered_items = _discovered_disease_matches(chunk, positions, pages, lines)
    else:
        return results
    for discovered in discovered_items:
        # Same per-chunk dedupe key as static entries: every guideline
        # section mentioning the disease keeps its own provenance row.
        key = (entity_type.entity_type, discovered["code"], chunk["chunk_id"])
        if key in seen:
            continue
        seen.add(key)
        match = discovered["match"]
        is_vocabulary_match = not discovered["code"].startswith("AUTO-")
        results.append({
            "entity_type": entity_type.entity_type,
            "name": discovered["name"],
            "code": discovered["code"],
            "concept_code": "",
            "chunk_id": chunk["chunk_id"],
            "page": discovered["page"],
            "line_start": discovered["line"],
            "line_end": discovered["line"],
            "quote": _match_quote(chunk["text"], match.start(), match.end()),
            "confidence": 0.8 if is_vocabulary_match else 0.62,
            "attributes": {},
        })
    return results