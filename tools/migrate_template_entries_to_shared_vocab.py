"""One-shot migration: move template-local entity entries into shared dictionaries.

Every template entity type with inline entries gets its entries copied to
data/vocab/entities/{entity_type}.json (first definition of a code wins),
then the type is switched to vocabulary=True with empty inline entries.
Re-running the script is a no-op.

Usage: python tools/migrate_template_entries_to_shared_vocab.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.extraction.repository import ExtractionTemplateRepository
from src.vocab.entity_vocabulary import ENTITY_VOCAB_DIR


def main(dry_run: bool = False) -> None:
    repo = ExtractionTemplateRepository()
    raw_items = repo._read()
    vocab_files: dict[str, list] = {}
    report = []

    for template in raw_items:
        template_id = template.get("template_id", "")
        for type_def in template.get("entity_types", []):
            entries = type_def.get("entries") or []
            if not entries and not type_def.get("vocabulary"):
                continue
            entity_type = type_def["entity_type"]
            if entity_type not in vocab_files:
                path = ENTITY_VOCAB_DIR / f"{entity_type}.json"
                vocab_files[entity_type] = (
                    json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
                )
            existing = vocab_files[entity_type]
            known = {item.get("code") for item in existing}
            moved, skipped = 0, []
            for entry in entries:
                if entry.get("code") in known:
                    skipped.append(entry.get("code"))
                    continue
                existing.append(entry)
                known.add(entry.get("code"))
                moved += 1
            report.append((template_id, entity_type, moved, skipped))
            type_def["vocabulary"] = True
            type_def["entries"] = []

    if dry_run:
        for template_id, entity_type, moved, skipped in report:
            print(f"[dry-run] {template_id} / {entity_type}: would move {moved}, skip dup {skipped}")
        return

    for entity_type, items in vocab_files.items():
        path = ENTITY_VOCAB_DIR / f"{entity_type}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    repo._write(raw_items)

    for template_id, entity_type, moved, skipped in report:
        print(f"{template_id} / {entity_type}: moved {moved}, skipped duplicate {skipped or '-'}")
    print(f"\nshared dictionaries written to {ENTITY_VOCAB_DIR}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
