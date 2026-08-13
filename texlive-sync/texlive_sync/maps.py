"""Generate updmap.cfg fragments from tlpdb ``execute addMap`` lines.

Same pattern as hyphen language fragments: packages drop a file under
``/usr/share/tlpkg/updmap.cfg.d/<pkg>``; ``texlive-rebuild-maps`` assembles
the live ``updmap.cfg`` and runs ``updmap-sys``.
"""

from __future__ import annotations

import re
from typing import Iterable

from .tlpdb import TLPackage

# execute addMap foo.map
# execute addMixedMap cm.map
# execute addKanjiMap otf-@jaEmbed@.map
_MAP = re.compile(
    r"^(?:execute\s+)?(?P<kind>addMap|addMixedMap|addKanjiMap)\s+(?P<file>\S+)\s*$",
    re.IGNORECASE,
)

_KIND_TO_DIRECTIVE = {
    "addmap": "Map",
    "addmixedmap": "MixedMap",
    "addkanjimap": "KanjiMap",
}


def parse_add_map_line(line: str) -> tuple[str, str] | None:
    """Return (Map|MixedMap|KanjiMap, filename) or None."""
    m = _MAP.match(line.strip())
    if not m:
        return None
    kind = _KIND_TO_DIRECTIVE[m.group("kind").lower()]
    return kind, m.group("file")


def map_entries(pkg: TLPackage) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ex in pkg.executes:
        e = parse_add_map_line(ex)
        if e is not None:
            out.append(e)
    return out


def has_map_executes(pkg: TLPackage) -> bool:
    return bool(map_entries(pkg))


def updmap_fragment(pkg_name: str, entries: Iterable[tuple[str, str]]) -> str:
    """Content for ``updmap.cfg.d/<pkg>``."""
    ents = list(entries)
    if not ents:
        return ""
    lines = [f"# from {pkg_name}:"]
    for kind, fn in ents:
        lines.append(f"{kind} {fn}")
    return "\n".join(lines) + "\n"


def map_fragment(pkg: TLPackage) -> str:
    return updmap_fragment(pkg.name, map_entries(pkg))
