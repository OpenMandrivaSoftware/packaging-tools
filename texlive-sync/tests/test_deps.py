import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from texlive_sync.deps import map_depend, rpm_requires
from texlive_sync.tlpdb import TLPackage

QUIRKS = {
    "system_packages": {"psutils": "psutils"},
    "extra_requires": {},
    "block": [],
}


def test_map_plain():
    assert map_depend("etoolbox", QUIRKS) == "texlive(etoolbox)"


def test_map_arch():
    assert map_depend("kpathsea.ARCH", QUIRKS) == "texlive(kpathsea.bin)"


def test_map_collection():
    assert map_depend("collection-basic", QUIRKS) == "texlive(collection-basic)"


def test_map_system():
    assert map_depend("psutils", QUIRKS) == "psutils"


def test_map_skip():
    assert map_depend("opt_foo:1", QUIRKS) is None


def test_requires():
    pkg = TLPackage(
        name="acmart",
        depends=["etoolbox", "l3kernel", "opt_x:1", "kpathsea.ARCH", "acmart"],
    )
    reqs = rpm_requires(pkg, QUIRKS)
    assert reqs == [
        "texlive(etoolbox)",
        "texlive(l3kernel)",
        "texlive(kpathsea.bin)",
    ]
