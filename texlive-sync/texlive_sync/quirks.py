"""Load quirks.yaml configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML subset if PyYAML is unavailable (maps of scalars / lists)."""
    root: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list | None = None
    current_map: dict | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0 and s.endswith(":") and not s.startswith("-"):
            current_key = s[:-1].strip()
            root[current_key] = None
            current_list = None
            current_map = None
            continue
        if current_key is None:
            continue
        if s.startswith("- "):
            if current_list is None:
                current_list = []
                root[current_key] = current_list
            current_list.append(_scalar(s[2:].strip()))
            current_map = None
        elif ":" in s and indent > 0:
            if current_map is None:
                current_map = {}
                root[current_key] = current_map
            k, v = s.split(":", 1)
            current_map[k.strip()] = _scalar(v.strip())
            current_list = None
        elif s == "{}":
            root[current_key] = {}
            current_list = None
            current_map = None
    return root


def _scalar(v: str) -> Any:
    if v == "" or v == "null" or v == "~":
        return None
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        return v


def load_quirks(path: Path | None) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "block": [],
        "extra_requires": {},
        "extra_provides": {},
        "epoch": {},
        "system_packages": {},
        "renames": {},
    }
    if path is None or not path.is_file():
        return defaults
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text) or {}
    else:
        data = _parse_minimal_yaml(text)
    for k, v in defaults.items():
        data.setdefault(k, v)
    return data
