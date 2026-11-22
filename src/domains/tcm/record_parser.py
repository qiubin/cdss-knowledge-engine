"""Conservative vocabulary matching for free-text TCM records."""
from __future__ import annotations

import re
from src.vocab.entity_vocabulary import EntityVocabularyRepository

KINDS = ("symptom", "tongue", "pulse", "sign", "constitution")
NEGATED = re.compile(r"(?:无|否认|未见|不伴|没有)$")


def parse_tcm_record(text: str) -> dict:
    found = []
    ambiguous = []
    for kind in KINDS:
        for entry in EntityVocabularyRepository(kind).list():
            names = sorted({entry.name, *entry.aliases}, key=len, reverse=True)
            for name in names:
                if len(name) < 2:
                    continue
                for match in re.finditer(re.escape(name), text):
                    prefix = text[max(0, match.start() - 3):match.start()]
                    item = {"observation_type": kind, "code": entry.code,
                            "name": entry.name, "matched_text": name,
                            "start": match.start(), "end": match.end(),
                            "present": not bool(NEGATED.search(prefix))}
                    if item["present"]:
                        found.append(item)
                    else:
                        ambiguous.append(item)
    # Longer matches win when one term contains another; preserve separate
    # observations such as tongue body and coating.
    found.sort(key=lambda item: (-(item["end"] - item["start"]), item["start"]))
    accepted = []
    occupied = set()
    for item in found:
        key = (item["observation_type"], item["code"])
        span = set(range(item["start"], item["end"]))
        if key in occupied or any(span & set(range(old["start"], old["end"])) for old in accepted):
            continue
        accepted.append(item)
        occupied.add(key)
    return {"observations": accepted, "negated": ambiguous,
            "requires_confirmation": True, "unmapped_terms": []}
