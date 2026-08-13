import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from texlive_sync.deps import map_depend, rpm_requires
from texlive_sync.tlpdb import TLPackage

QUIRKS = {
    "system_packages": {
        "psutils": "psutils",
        "texlive.infra": "texlive-tlpkg",
    },
    "extra_requires": {},
    "block": ["texlive.infra", "00texlive.config"],
}


def test_map_plain():
    assert map_depend("etoolbox", QUIRKS) == "texlive(etoolbox)"


def test_map_arch():
    assert map_depend("kpathsea.ARCH", QUIRKS) == "texlive(kpathsea.bin)"


def test_map_collection():
    assert map_depend("collection-basic", QUIRKS) == "texlive(collection-basic)"


def test_map_system():
    assert map_depend("psutils", QUIRKS) == "psutils"


def test_map_infra_to_tlpkg():
    assert map_depend("texlive.infra", QUIRKS) == "texlive-tlpkg"
    assert map_depend("texlive.infra.ARCH", QUIRKS) is None


def test_map_blocked_unmapped_skipped():
    assert map_depend("00texlive.config", QUIRKS) is None


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


def test_requires_infra_maps_to_tlpkg():
    pkg = TLPackage(
        name="collection-basic",
        depends=["amsfonts", "texlive.infra", "texlive-scripts"],
    )
    reqs = rpm_requires(pkg, QUIRKS)
    assert reqs == [
        "texlive(amsfonts)",
        "texlive-tlpkg",
        "texlive(texlive-scripts)",
    ]


def test_extra_requires_scripts_need_gsftopk():
    from texlive_sync.quirks import load_quirks

    quirks = load_quirks(Path(__file__).resolve().parents[1] / "quirks.yaml")
    pkg = TLPackage(
        name="texlive-scripts",
        depends=["texlive-scripts.ARCH", "texlive.infra"],
    )
    reqs = rpm_requires(pkg, quirks)
    assert "texlive(gsftopk)" in reqs
    assert "texlive(mfware)" in reqs
    assert "texlive-tlpkg" in reqs
