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
