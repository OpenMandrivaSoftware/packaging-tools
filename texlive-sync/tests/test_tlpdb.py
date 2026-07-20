import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from texlive_sync.tlpdb import is_skippable_depend, parse_tlpdb

SAMPLE = """
name acmart
category Package
revision 79508
shortdesc Class for ACM
longdesc This package provides a class.
depend etoolbox
depend l3kernel
depend l3packages
catalogue-version 2.19
catalogue-license lppl1.3
catalogue-ctan /macros/latex/contrib/acmart
containersize 100
doccontainersize 200
runfiles size=1
 RELOC/tex/latex/acmart/acmart.cls
docfiles size=1
 RELOC/doc/latex/acmart/README

name collection-latex
category Collection
revision 73720
shortdesc LaTeX fundamental packages
depend collection-basic
depend babel
depend latex

name a2ping.x86_64-linux
category Package
revision 1
"""


def test_parse_acmart():
    pkgs = parse_tlpdb(SAMPLE)
    p = pkgs["acmart"]
    assert p.revision == 79508
    assert p.catalogue_version == "2.19"
    assert p.depends == ["etoolbox", "l3kernel", "l3packages"]
    assert p.has_doc_container
    assert "RELOC/tex/latex/acmart/acmart.cls" in p.runfiles


def test_skip_arch():
    pkgs = parse_tlpdb(SAMPLE)
    assert pkgs["a2ping.x86_64-linux"].is_arch_package


def test_skippable():
    assert is_skippable_depend("opt_autobackup:1")
    assert is_skippable_depend("container_format/xz")
    assert not is_skippable_depend("etoolbox")


def test_collection():
    pkgs = parse_tlpdb(SAMPLE)
    assert pkgs["collection-latex"].is_collection
