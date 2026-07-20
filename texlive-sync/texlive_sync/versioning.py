"""RPM Version/Release policy for TeX Live packages."""

from __future__ import annotations

import re


# RPM Version allows alphanumerics plus . _ + ~
_INVALID_VER_CHARS = re.compile(r"[^A-Za-z0-9._+~]")


def sanitize_catalogue_version(raw: str) -> str:
    """Make a CTAN catalogue-version safe for RPM Version:."""
    v = raw.strip()
    if not v:
        return ""
    # Hyphen is illegal in Version; tilde sorts before final releases.
    v = v.replace("-", "~")
    v = v.replace(" ", ".")
    v = _INVALID_VER_CHARS.sub("", v)
    # Collapse empty leftovers
    v = re.sub(r"[.]+", ".", v).strip(".")
    return v


def rpm_version_release(
    revision: int,
    catalogue_version: str | None,
    packrel: int = 1,
) -> tuple[str, str]:
    """
    Return (Version, Release) per OpenMandriva policy:

    * With catalogue-version: Version=sanitized, Release=<revision>.<packrel>
    * Without: Version=<revision>, Release=<packrel>   (not 0; not %{?dist})
    """
    cat = sanitize_catalogue_version(catalogue_version or "")
    if cat:
        return cat, f"{revision}.{packrel}"
    return str(revision), str(packrel)
