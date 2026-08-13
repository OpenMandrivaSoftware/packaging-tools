"""Generate language.dat / language.def / language.dat.lua fragments from tlpdb.

Mirrors TeX Live's TLPOBJ::language_*_lines for ``execute AddHyphen ...``.
Fragments install under ``/usr/share/tlpkg/language.{dat,def,lua}.d/<pkg>``
and are assembled by ``texlive-rebuild-hyphen`` (filetrigger on texlive-tlpkg).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .tlpdb import TLPackage

_ADD_HYPHEN = re.compile(r"^AddHyphen\s+(.*)$", re.IGNORECASE)


@dataclass
class HyphenEntry:
    name: str
    file: str
    lefthyphenmin: int = 2
    righthyphenmin: int = 2
    synonyms: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=lambda: ["dat", "def", "lua"])
    file_patterns: str | None = None
    file_exceptions: str | None = None
    luaspecial: str | None = None
    comment: str | None = None


def parse_add_hyphen_line(line: str) -> HyphenEntry | None:
    """
    Parse the argument part of ``execute AddHyphen name=... file=...``.

    Returns None if the line is not an AddHyphen execute.
    """
    s = line.strip()
    if s.lower().startswith("execute "):
        s = s[8:].strip()
    m = _ADD_HYPHEN.match(s)
    if not m:
        return None
    rest = m.group(1)
    # Tokenize on whitespace; values are key=value (synonyms may be comma lists).
    fields: dict[str, str] = {}
    for tok in rest.split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        fields[k] = v

    name = fields.get("name")
    fpath = fields.get("file")
    if not name or not fpath:
        return None

    def _int(key: str, default: int) -> int:
        raw = fields.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    syn = fields.get("synonyms") or ""
    synonyms = [x for x in syn.split(",") if x]
    dbs = fields.get("databases")
    databases = [x for x in dbs.split(",")] if dbs else ["dat", "def", "lua"]

    return HyphenEntry(
        name=name,
        file=fpath,
        lefthyphenmin=_int("lefthyphenmin", 2),
        righthyphenmin=_int("righthyphenmin", 2),
        synonyms=synonyms,
        databases=databases,
        file_patterns=fields.get("file_patterns") or None,
        file_exceptions=fields.get("file_exceptions") or None,
        luaspecial=fields.get("luaspecial") or None,
        comment=fields.get("comment") or None,
    )


def hyphen_entries(pkg: TLPackage) -> list[HyphenEntry]:
    out: list[HyphenEntry] = []
    for ex in pkg.executes:
        e = parse_add_hyphen_line(ex)
        if e is not None:
            out.append(e)
    return out


def has_hyphen_executes(pkg: TLPackage) -> bool:
    return bool(hyphen_entries(pkg))


def language_dat_fragment(pkg_name: str, entries: Iterable[HyphenEntry]) -> str:
    """Content for ``language.dat.d/<pkg>``."""
    lines: list[str] = [f"% from {pkg_name}:"]
    any_dat = False
    for e in entries:
        if "dat" not in e.databases:
            continue
        any_dat = True
        if e.comment:
            lines.append(f"% {e.comment}")
        lines.append(f"{e.name} {e.file}")
        for syn in e.synonyms:
            lines.append(f"={syn}")
    if not any_dat:
        return ""
    return "\n".join(lines) + "\n"


def language_def_fragment(pkg_name: str, entries: Iterable[HyphenEntry]) -> str:
    """Content for ``language.def.d/<pkg>``."""
    lines: list[str] = [f"% from {pkg_name}:"]
    any_def = False
    for e in entries:
        if "def" not in e.databases:
            continue
        any_def = True
        if e.comment:
            lines.append(f"% {e.comment}")
        # empty exceptions file argument (same as TLPOBJ)
        lines.append(
            f"\\addlanguage{{{e.name}}}{{{e.file}}}{{}}{{{e.lefthyphenmin}}}{{{e.righthyphenmin}}}"
        )
        for syn in e.synonyms:
            lines.append(
                f"\\addlanguage{{{syn}}}{{{e.file}}}{{}}{{{e.lefthyphenmin}}}{{{e.righthyphenmin}}}"
            )
    if not any_def:
        return ""
    return "\n".join(lines) + "\n"


def language_lua_fragment(pkg_name: str, entries: Iterable[HyphenEntry]) -> str:
    """Content for ``language.lua.d/<pkg>`` (table entries only)."""
    lines: list[str] = [f"-- from {pkg_name}:"]
    any_lua = False
    for e in entries:
        if "lua" not in e.databases:
            continue
        any_lua = True
        syns = ", ".join(f"'{s}'" for s in e.synonyms)
        lines.append(f"['{e.name}'] = {{")
        lines.append(f"\tloader = '{e.file}',")
        lines.append(f"\tlefthyphenmin = {e.lefthyphenmin},")
        lines.append(f"\trighthyphenmin = {e.righthyphenmin},")
        lines.append(f"\tsynonyms = {{ {syns} }},")
        if e.file_patterns:
            lines.append(f"\tpatterns = '{e.file_patterns}',")
        if e.file_exceptions:
            lines.append(f"\thyphenation = '{e.file_exceptions}',")
        if e.luaspecial:
            lines.append(f"\tspecial = '{e.luaspecial}',")
        lines.append("},")
    if not any_lua:
        return ""
    return "\n".join(lines) + "\n"


def language_fragments(pkg: TLPackage) -> dict[str, str]:
    """
    Map kind → fragment body for kinds that have content.

    Keys: ``dat``, ``def``, ``lua``.
    """
    entries = hyphen_entries(pkg)
    if not entries:
        return {}
    out: dict[str, str] = {}
    dat = language_dat_fragment(pkg.name, entries)
    if dat:
        out["dat"] = dat
    defn = language_def_fragment(pkg.name, entries)
    if defn:
        out["def"] = defn
    lua = language_lua_fragment(pkg.name, entries)
    if lua:
        out["lua"] = lua
    return out
