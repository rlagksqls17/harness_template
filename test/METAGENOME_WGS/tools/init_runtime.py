#!/usr/bin/env python3
"""Create missing local runtime state without overwriting user data."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_json_if_missing(path: Path, payload: object, created: list[Path], kept: list[Path]) -> None:
    if path.exists():
        kept.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    created.append(path)


def touch_if_missing(path: Path, created: list[Path], kept: list[Path]) -> None:
    if path.exists():
        kept.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    created.append(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Reserved for compatibility; overwriting is intentionally unsupported.")
    args = parser.parse_args()
    if args.force:
        print("ERROR: --force is not supported because runtime state must never be overwritten.")
        return 2

    registry = json.loads((ROOT / "system" / "role_registry.json").read_text(encoding="utf-8"))
    created: list[Path] = []
    kept: list[Path] = []
    now = datetime.now(timezone.utc).isoformat()

    for agent in registry["agents"]:
        role_path = ROOT / agent["path"]
        agent_dir = role_path.parent.parent
        write_json_if_missing(
            agent_dir / "memory" / "current.json",
            {
                "agent_id": agent["id"],
                "revision": 0,
                "fixed": {},
                "current": {"status": "idle", "task_id": None, "stage": None},
                "references": [],
                "updated_at": now,
            },
            created,
            kept,
        )
        touch_if_missing(agent_dir / "record" / "history.jsonl", created, kept)

    current_task = ROOT / "system" / "current_task.yaml"
    if current_task.exists():
        kept.append(current_task)
    else:
        shutil.copyfile(ROOT / "system" / "task_template.yaml", current_task)
        created.append(current_task)

    system_state = ROOT / "system" / "system_state.json"
    if system_state.exists():
        kept.append(system_state)
    else:
        shutil.copyfile(ROOT / "system" / "system_state.example.json", system_state)
        created.append(system_state)

    for name in ("ledger.jsonl", "evidence_index.jsonl"):
        touch_if_missing(ROOT / "records" / name, created, kept)

    repository_root = ROOT.parents[1]
    mail_root = repository_root / "reference" / "mail"
    extracted_mail = sorted(mail_root.glob("*/Takeout/메일")) if mail_root.exists() else []
    mail_archives = sorted(mail_root.glob("*.zip")) if mail_root.exists() else []
    mail_candidates = extracted_mail or mail_archives
    mail_path = str(mail_candidates[0]) if mail_candidates else "<LOCAL_MAIL_ARCHIVE_PATH>"
    record_candidate = repository_root / "reference" / "record"
    record_path = str(record_candidate) if record_candidate.exists() else "<LOCAL_RECORD_DIRECTORY>"

    write_json_if_missing(
        ROOT / "reference" / "reference_registry.local.json",
        {
            "version": "1.0.0",
            "references": [
                {
                    "id": "mail_archive",
                    "path": mail_path,
                    "kind": "communication",
                    "access": "read_only",
                    "sensitivity": "S2",
                },
                {
                    "id": "work_records",
                    "path": record_path,
                    "kind": "document",
                    "access": "read_only",
                    "sensitivity": "S1",
                },
            ],
            "rules": ["local_only", "never_store_credentials", "verify_path_before_use"],
            "owner": "user",
            "default_access": "read_only",
            "updated_at": now,
        },
        created,
        kept,
    )

    print(f"RUNTIME_INIT_OK created={len(created)} kept={len(kept)}")
    for path in created:
        print(f"CREATED {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
