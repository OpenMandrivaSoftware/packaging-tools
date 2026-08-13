import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from texlive_sync.versioning import rpm_version_release, sanitize_catalogue_version


def test_sanitize_hyphen():
    assert sanitize_catalogue_version("1.9.6-2") == "1.9.6~2"


def test_sanitize_letters():
    assert sanitize_catalogue_version("8.31b") == "8.31b"


def test_with_catalogue():
    v, r = rpm_version_release(79508, "2.19")
    assert v == "2.19"
    assert r == "79508.1"


def test_without_catalogue_uses_revision():
    v, r = rpm_version_release(15878, None)
    assert v == "15878"
    assert r == "1"


def test_empty_catalogue_uses_revision():
    v, r = rpm_version_release(15878, "  ")
    assert v == "15878"
    assert r == "1"


def test_generate_spec_identifies_catalogue_version():
    from texlive_sync.generate import generate_spec
    from texlive_sync.tlpdb import TLPackage

    pkg = TLPackage(
        name="abntexto",
        revision=78949,
        catalogue_version="1.1",
        catalogue_license="pd",
        shortdesc="LaTeX class for formatting academic papers in ABNT standards",
    )
    spec = generate_spec(pkg, {})
    assert "%global tl_version 1.1" in spec
    assert "Version:\t%{tl_version}" in spec
    assert "Release:\t%{tl_revision}.1" in spec
    assert "Provides:\ttexlive(%{tl_name}) = %{version}" in spec
    assert "Provides:\ttexlive(%{tl_name}) = %{tl_revision}" not in spec


def test_generate_spec_sanitizes_beta_catalogue_version():
    from texlive_sync.generate import generate_spec
    from texlive_sync.tlpdb import TLPackage

    pkg = TLPackage(
        name="example",
        revision=100,
        catalogue_version="4.0.5-beta",
        catalogue_license="lppl1.3",
        shortdesc="Example",
    )
    spec = generate_spec(pkg, {})
    assert "%global tl_version 4.0.5~beta" in spec
    assert "Version:\t%{tl_version}" in spec
