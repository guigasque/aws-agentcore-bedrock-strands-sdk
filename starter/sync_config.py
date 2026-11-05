"""Mirror config.yaml values into the three literal assignments in main.py.

The provided setup_permissions.py reads REGION, KB_ID and MEMORY_ID from main.py by
AST-parsing literal string assignments. config.yaml is the single source of truth; this
script keeps those three literals in sync without hand-editing main.py.

Mapping (config.yaml -> main.py literal):
    region             -> REGION
    knowledge_base_id  -> KB_ID
    memory_id          -> MEMORY_ID

The script is idempotent: running it when the literals already match is a no-op. It only
rewrites the specific assignment lines, leaving the rest of main.py untouched. It does not
import or execute main.py.

Usage:
    python sync_config.py                 # sync using ./config.yaml and ./main.py
    python sync_config.py --check         # exit non-zero if out of sync (no write)
    python sync_config.py --config ... --main ...
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# config.yaml key -> main.py variable name.
KEY_TO_VAR: dict[str, str] = {
    "region": "REGION",
    "knowledge_base_id": "KB_ID",
    "memory_id": "MEMORY_ID",
}

# Same shape checks setup_permissions.py enforces. Placeholders (e.g. "<kbid>") are allowed
# here so the repo stays runnable before provisioning; they simply will not pass the real
# permissions step until infra/outputs.py fills them in.
SHAPE = {
    "REGION": re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d+"),
    "KB_ID": re.compile(r"[A-Za-z0-9]{10}"),
    "MEMORY_ID": re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}-[A-Za-z0-9]{10}"),
}

PLACEHOLDER = re.compile(r"^<.*>$")


def load_values(config_path: Path) -> dict[str, str]:
    """Read the three mirrored values from config.yaml as strings."""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml must be a YAML mapping.")
    values: dict[str, str] = {}
    for key, var in KEY_TO_VAR.items():
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"config.yaml is missing a string value for '{key}'.")
        if not PLACEHOLDER.match(value) and not SHAPE[var].fullmatch(value):
            raise ValueError(
                f"config.yaml '{key}'={value!r} does not match the expected shape for {var}."
            )
        values[var] = value
    return values


def replace_literal(source: str, var: str, value: str) -> tuple[str, bool]:
    """Set `VAR = "value"` in source. Returns (new_source, changed).

    Matches a top-level assignment to VAR (optionally annotated) regardless of the current
    right-hand side, and rewrites only that line. Raises if the assignment is not found.
    """
    # VAR = <anything>   or   VAR: Type = <anything>  (rest of line, incl. trailing comment).
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*)(?P<name>{re.escape(var)})(?P<ann>[ \t]*:[^=\n]+)?"
        rf"(?P<pre>[ \t]*=[ \t]*)(?P<rhs>.*)$",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"Could not find an assignment for {var} in main.py.")

    desired_rhs = f'"{value}"'
    # Preserve a trailing comment if the existing RHS has one.
    existing_rhs = match.group("rhs")
    comment = ""
    in_string = False
    quote = ""
    for i, ch in enumerate(existing_rhs):
        if in_string:
            if ch == quote:
                in_string = False
        elif ch in ("'", '"'):
            in_string = True
            quote = ch
        elif ch == "#":
            comment = existing_rhs[i:].rstrip()
            break
    new_rhs = desired_rhs + (f"  {comment}" if comment else "")

    if existing_rhs.strip() == new_rhs.strip():
        return source, False

    start, end = match.span()
    rebuilt = (
        f"{match.group('indent')}{match.group('name')}"
        f"{match.group('ann') or ''}{match.group('pre')}{new_rhs}"
    )
    return source[:start] + rebuilt + source[end:], True


def sync(config_path: Path, main_path: Path, check_only: bool) -> int:
    """Sync (or check) the three literals. Returns a process exit code."""
    values = load_values(config_path)
    source = main_path.read_text(encoding="utf-8")

    changed_vars: list[str] = []
    new_source = source
    for var, value in values.items():
        new_source, changed = replace_literal(new_source, var, value)
        if changed:
            changed_vars.append(var)

    if not changed_vars:
        print("main.py literals already match config.yaml; nothing to do.")
        return 0

    if check_only:
        print(f"Out of sync: {', '.join(changed_vars)} differ from config.yaml.", file=sys.stderr)
        return 1

    main_path.write_text(new_source, encoding="utf-8")
    print(f"Synced into main.py: {', '.join(changed_vars)}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config.yaml")
    parser.add_argument("--main", type=Path, default=root / "main.py")
    parser.add_argument(
        "--check", action="store_true", help="Exit non-zero if out of sync; do not write."
    )
    args = parser.parse_args(argv)
    try:
        return sync(args.config, args.main, args.check)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"sync_config failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
