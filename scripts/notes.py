#!/usr/bin/env python3
"""Sync timeline notes between local meta.json and a shareable seed file.

Usage:
  python3 scripts/notes.py export   # data/meta.json:notes  →  notes-seed.json   (commit this)
  python3 scripts/notes.py import   # notes-seed.json       →  data/meta.json:notes
                                    #   safe: additive only, never overwrites local notes.
  python3 scripts/notes.py status   # show what's where

Photos and hashes stay in data/meta.json (gitignored). Only the `notes` dict
travels in notes-seed.json so the timeline can ride along with `git pull`.

DATA_DIR env var works the same as it does for server.py (defaults to ./data).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "notes-seed.json"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve()
META = DATA_DIR / "meta.json"


def _load(p: Path):
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def cmd_export():
    meta = _load(META) or {"files": {}, "notes": {}}
    notes = meta.get("notes", {})
    _save(SEED, {"notes": notes, "_meta": {"exported_from": str(META)}})
    print(f"✓ exported {len(notes)} note(s) → {SEED.relative_to(ROOT)}")
    print(f"  next:  git add notes-seed.json && git commit -m 'Update notes timeline' && git push")


def cmd_import():
    seed = _load(SEED)
    if not seed:
        print(f"✗ {SEED} not found. On the source machine run:  python3 scripts/notes.py export")
        sys.exit(1)
    seed_notes = seed.get("notes", {})

    meta = _load(META) or {"files": {}, "notes": {}}
    meta.setdefault("files", {})
    meta.setdefault("notes", {})

    added = skipped = 0
    for nid, rec in seed_notes.items():
        if nid in meta["notes"]:
            skipped += 1
        else:
            meta["notes"][nid] = rec
            added += 1
    _save(META, meta)
    print(f"✓ merged into {META}")
    print(f"  added:   {added}  (new notes pulled from seed)")
    print(f"  skipped: {skipped}  (already present locally — not overwritten)")
    if added:
        print(f"  refresh the gallery in your browser to see them.")


def cmd_status():
    print(f"DATA_DIR  : {DATA_DIR}")
    print(f"meta.json : {META}  ({'exists' if META.is_file() else 'missing'})")
    print(f"seed file : {SEED}  ({'exists' if SEED.is_file() else 'missing'})")
    if META.is_file():
        m = _load(META)
        print(f"  local notes : {len(m.get('notes', {}))}")
        print(f"  local files : {len(m.get('files', {}))}")
    if SEED.is_file():
        s = _load(SEED)
        print(f"  seed notes  : {len(s.get('notes', {}))}")


if __name__ == "__main__":
    cmds = {"export": cmd_export, "import": cmd_import, "status": cmd_status}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
