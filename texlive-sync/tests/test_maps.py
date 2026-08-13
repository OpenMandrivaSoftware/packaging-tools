"""Tests for addMap → updmap.cfg fragment generation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from texlive_sync.generate import generate_spec
from texlive_sync.maps import map_fragment, parse_add_map_line
from texlive_sync.tlpdb import TLPackage


def test_parse_add_map_kinds():
    assert parse_add_map_line("addMap fontawesome5.map") == (
        "Map",
        "fontawesome5.map",
    )
    assert parse_add_map_line("execute addMixedMap cm.map") == (
        "MixedMap",
        "cm.map",
    )
    assert parse_add_map_line("addKanjiMap otf-@jaEmbed@.map") == (
        "KanjiMap",
        "otf-@jaEmbed@.map",
    )
    assert parse_add_map_line("AddHyphen name=french file=x.tex") is None


def test_fragment_and_spec():
    pkg = TLPackage(
        name="fontawesome5",
        revision=77682,
        catalogue_version="5.15.4",
        executes=["addMap fontawesome5.map"],
        shortdesc="Font Awesome 5",
    )
    body = map_fragment(pkg)
    assert "# from fontawesome5:" in body
    assert "Map fontawesome5.map" in body
    spec = generate_spec(pkg, {})
    assert "Requires:\ttexlive-tlpkg" in spec
    assert "%{_texmf_updmap_d}/%{tl_name}" in spec
    assert "Map fontawesome5.map" in spec
    assert "%install -a" in spec
