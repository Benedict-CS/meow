#!/usr/bin/env python3
"""Sync timeline notes between local meta.json and a shareable seed file.

Commands:
  export   data/meta.json:notes  ->  notes-seed.json     (commit this)
  import   notes-seed.json       ->  data/meta.json      (additive merge, never overwrites)
  status   show where notes live and how many there are on each side

Photos and hashes stay in data/meta.json (gitignored). Only the `notes` dict
travels in notes-seed.json so the timeline can ride along with `git pull`.

Environment:
  DATA_DIR    same semantics as server.py — where data/meta.json lives
              (default: ./data alongside this repo)
  NO_COLOR    set to disable ANSI colors (also auto-off on non-TTY / Windows
              consoles that don't speak ANSI)
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "notes-seed.json"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve()
META = DATA_DIR / "meta.json"


# ---------- Terminal colors (stdlib only, portable) ----------
class _Style:
    """ANSI color codes. All attributes become empty strings when colors are off."""
    _CODES = {
        "RESET":  "\033[0m",
        "BOLD":   "\033[1m",
        "DIM":    "\033[2m",
        "RED":    "\033[31m",
        "GREEN":  "\033[32m",
        "YELLOW": "\033[33m",
        "BLUE":   "\033[34m",
        "MAGENTA":"\033[35m",
        "CYAN":   "\033[36m",
        "GREY":   "\033[90m",
    }

    def __init__(self, enabled: bool):
        self.enabled = enabled
        for k, v in self._CODES.items():
            setattr(self, k, v if enabled else "")


def _color_supported(force_off: bool) -> bool:
    """ANSI colors when stdout is a real TTY, no NO_COLOR override, and we
    can reasonably expect the terminal to handle escapes. On Windows we try
    to enable Virtual Terminal Processing; if that fails we disable color."""
    if force_off or os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Modern Windows 10+ consoles support ANSI but only after VT mode is on.
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                return False
        except Exception:
            return False
    return True


# Module-level style handle; rebound in main() once we know --no-color.
S = _Style(enabled=False)


# ---------- Pretty printers ----------
def _header(title: str):
    bar = "──"
    print(f"\n{S.BOLD}{S.CYAN}{bar} {title} {bar}{S.RESET}")


def _ok(msg: str):
    print(f"{S.GREEN}✓{S.RESET} {msg}")


def _warn(msg: str):
    print(f"{S.YELLOW}!{S.RESET} {msg}")


def _err(msg: str):
    print(f"{S.RED}✗{S.RESET} {msg}", file=sys.stderr)


def _hint(msg: str):
    print(f"  {S.DIM}→ {msg}{S.RESET}")


def _kv(key: str, value, suffix: str = ""):
    print(f"  {S.GREY}{key:<12}{S.RESET} {value}{suffix}")


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


# ---------- File IO ----------
def _load(p: Path):
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _err(f"{p} is not valid JSON: {e}")
        _hint("restore it from a backup, or delete it to start fresh")
        sys.exit(2)
    except OSError as e:
        _err(f"could not read {p}: {e}")
        sys.exit(2)


def _save(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError as e:
        _err(f"could not write {p}: {e}")
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass
        sys.exit(2)


def _try_relative(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# ---------- Commands ----------
def cmd_export(_args):
    _header("Export notes")
    meta = _load(META)
    if meta is None:
        _err(f"{META} not found — nothing to export")
        _hint(f"run the gallery server once so {META.name} gets created,")
        _hint("or check that DATA_DIR points at your data folder")
        sys.exit(1)

    notes = meta.get("notes", {})
    _save(SEED, {"notes": notes, "_meta": {"exported_from": str(META)}})

    try:
        size = SEED.stat().st_size
    except OSError:
        size = 0

    _ok(f"exported {S.BOLD}{len(notes)}{S.RESET} note(s) "
        f"-> {S.BOLD}{_try_relative(SEED)}{S.RESET}")
    _kv("file size",  _human_bytes(size))
    _kv("source",     _try_relative(META))

    if notes:
        _header("Next step")
        _hint("git add notes-seed.json")
        _hint("git commit -m 'Update notes timeline'")
        _hint("git push")
    else:
        _warn("no notes yet — nothing to commit")


def cmd_import(_args):
    _header("Import notes")
    seed = _load(SEED)
    if seed is None:
        _err(f"{_try_relative(SEED)} not found")
        _hint("on the source machine run:  python3 scripts/notes.py export")
        _hint("then git push, git pull here, and re-run this command")
        sys.exit(1)

    seed_notes = seed.get("notes", {})
    if not isinstance(seed_notes, dict):
        _err(f"{_try_relative(SEED)} has no `notes` dict")
        _hint("the seed file is malformed — regenerate it with `notes.py export`")
        sys.exit(2)

    meta = _load(META) or {"files": {}, "notes": {}}
    meta.setdefault("files", {})
    meta.setdefault("notes", {})

    pre_count = len(meta["notes"])
    added = skipped = 0
    for nid, rec in seed_notes.items():
        if nid in meta["notes"]:
            skipped += 1
        else:
            meta["notes"][nid] = rec
            added += 1

    _save(META, meta)
    post_count = len(meta["notes"])

    _ok(f"merged into {S.BOLD}{_try_relative(META)}{S.RESET}")

    # Short summary diff — what changed, what was already there.
    added_str   = f"{S.GREEN}+{added} new{S.RESET}"
    skipped_str = f"{S.DIM}{skipped} already present{S.RESET}"
    print(f"  {added_str}, {skipped_str}  "
          f"({S.GREY}local notes: {pre_count} -> {post_count}{S.RESET})")

    if added:
        _hint("refresh the gallery in your browser to see the new notes")
    elif skipped:
        _ok("already up to date — nothing to do")


def cmd_status(_args):
    _header("Notes status")
    _kv("DATA_DIR",  DATA_DIR)

    meta_exists = META.is_file()
    seed_exists = SEED.is_file()

    meta_status = (f"{S.GREEN}exists{S.RESET}" if meta_exists
                   else f"{S.YELLOW}missing{S.RESET}")
    seed_status = (f"{S.GREEN}exists{S.RESET}" if seed_exists
                   else f"{S.YELLOW}missing{S.RESET}")
    _kv("meta.json",  META,  f"  [{meta_status}]")
    _kv("seed file",  SEED,  f"  [{seed_status}]")

    local_notes = local_files = seed_notes = 0
    if meta_exists:
        m = _load(META) or {}
        local_notes = len(m.get("notes", {}) or {})
        local_files = len(m.get("files", {}) or {})
    if seed_exists:
        s = _load(SEED) or {}
        seed_notes = len(s.get("notes", {}) or {})

    _header("Counts")
    if meta_exists:
        _kv("local notes", local_notes)
        _kv("local files", local_files)
    else:
        _warn("meta.json missing — start the gallery server once to create it")

    if seed_exists:
        _kv("seed notes",  seed_notes)
    else:
        _warn("no seed file yet — run `notes.py export` to create one")

    # Pending diff — what `import` would do right now.
    if meta_exists and seed_exists:
        m = _load(META) or {}
        s = _load(SEED) or {}
        s_keys = set((s.get("notes") or {}).keys())
        m_keys = set((m.get("notes") or {}).keys())
        pending = len(s_keys - m_keys)
        _header("Sync")
        if pending == 0:
            _ok("in sync — `import` would be a no-op")
        else:
            _warn(f"{pending} note(s) in seed not yet imported locally")
            _hint("run:  python3 scripts/notes.py import")


# ---------- Entry point ----------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notes.py",
        description=(
            "Sync timeline notes between data/meta.json and notes-seed.json. "
            "Import is additive — local notes are never overwritten."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/notes.py export    # dump notes -> notes-seed.json\n"
            "  python3 scripts/notes.py import    # merge seed into meta.json\n"
            "  python3 scripts/notes.py status    # show what's on each side\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colors (also off automatically on non-TTY output)",
    )

    sub = parser.add_subparsers(dest="command", metavar="{export,import,status}")
    sub.required = True

    sub.add_parser(
        "export",
        help="dump local notes into notes-seed.json (commit this to git)",
        description="Write data/meta.json:notes to notes-seed.json so the "
                    "timeline can travel with git push/pull.",
    )
    sub.add_parser(
        "import",
        help="merge notes-seed.json into local meta.json (additive, idempotent)",
        description="Add any notes from notes-seed.json that aren't already in "
                    "data/meta.json. Existing local notes are never overwritten, "
                    "so re-running is safe.",
    )
    sub.add_parser(
        "status",
        help="show where each file lives and how many notes are on each side",
        description="Quick read-only summary of DATA_DIR, the meta.json + seed "
                    "file paths, their note counts, and what `import` would do.",
    )
    return parser


def main(argv=None):
    global S
    parser = _build_parser()
    args = parser.parse_args(argv)

    S = _Style(enabled=_color_supported(force_off=args.no_color))

    handlers = {
        "export": cmd_export,
        "import": cmd_import,
        "status": cmd_status,
    }
    try:
        handlers[args.command](args)
    except KeyboardInterrupt:
        _err("interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
