"""Tests for AddHyphen → language.dat fragment generation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from texlive_sync.hyphen import (
    language_dat_fragment,
    language_def_fragment,
    language_fragments,
    language_lua_fragment,
    parse_add_hyphen_line,
)
from texlive_sync.tlpdb import TLPackage
from texlive_sync.generate import generate_spec


def test_parse_add_hyphen_basic():
    e = parse_add_hyphen_line(
        "AddHyphen name=french synonyms=patois,francais "
        "lefthyphenmin=2 righthyphenmin=2 file=loadhyph-fr.tex "
        "file_patterns=hyph-fr.pat.txt file_exceptions="
    )
    assert e is not None
    assert e.name == "french"
    assert e.file == "loadhyph-fr.tex"
    assert e.synonyms == ["patois", "francais"]
    assert e.lefthyphenmin == 2
    assert e.righthyphenmin == 2


def test_dat_fragment_format():
    pkg = TLPackage(
        name="hyphen-french",
        executes=[
            "AddHyphen name=french synonyms=patois,francais "
            "lefthyphenmin=2 righthyphenmin=2 file=loadhyph-fr.tex "
            "file_patterns=hyph-fr.pat.txt file_exceptions="
        ],
    )
    frags = language_fragments(pkg)
    assert "dat" in frags
    body = frags["dat"]
    assert "% from hyphen-french:" in body
    assert "french loadhyph-fr.tex" in body
    assert "=patois" in body
    assert "=francais" in body


def test_def_and_lua_fragments():
    entries = [
        parse_add_hyphen_line(
            "AddHyphen name=german lefthyphenmin=2 righthyphenmin=2 "
            "file=loadhyph-de-1901.tex file_patterns=hyph-de-1901.pat.txt "
            "file_exceptions="
        )
    ]
    assert entries[0] is not None
    defn = language_def_fragment("hyphen-german", entries)
    assert r"\addlanguage{german}{loadhyph-de-1901.tex}{}{2}{2}" in defn
    lua = language_lua_fragment("hyphen-german", entries)
    assert "['german'] = {" in lua
    assert "loader = 'loadhyph-de-1901.tex'" in lua


def test_generate_spec_emits_install_dropins():
    pkg = TLPackage(
        name="hyphen-french",
        revision=78069,
        depends=["hyph-utf8", "hyphen-base"],
        executes=[
            "AddHyphen name=french synonyms=patois,francais "
            "lefthyphenmin=2 righthyphenmin=2 file=loadhyph-fr.tex "
            "file_patterns=hyph-fr.pat.txt file_exceptions="
        ],
        shortdesc="French hyphenation patterns.",
    )
    spec = generate_spec(pkg, {})
    assert "Requires:\ttexlive-tlpkg" in spec
    assert "%install -a" in spec
    assert "%{_texmf_language_dat_d}/%{tl_name}" in spec
    assert "french loadhyph-fr.tex" in spec
    assert "%{_texmf_language_def_d}" in spec
    assert "%{_texmf_language_lua_d}" in spec


def test_no_hyphen_no_install_append():
    pkg = TLPackage(name="etoolbox", revision=1, shortdesc="tools")
    spec = generate_spec(pkg, {})
    assert "%install -a" not in spec
    assert "language_dat_d" not in spec
