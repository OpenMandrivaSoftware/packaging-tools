"""Parse and fetch TeX Live package database (texlive.tlpdb)."""

from __future__ import annotations

import lzma
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DEFAULT_MIRROR = "https://mirrors.ctan.org/systems/texlive/tlnet"
TLPDB_XZ = "tlpkg/texlive.tlpdb.xz"
ARCHIVE = "archive"

# Installer / config depends — not RPM packages
_SKIP_DEPEND = re.compile(
    r"^(opt_|container_|revision/|release/|minrelease/|frozen/|config_)"
)

# Architecture-specific package suffixes in tlpdb names (pkg.ARCH).
# ARCH is typically cpu-os, e.g. x86_64-linux, x86_64-linuxmusl, universal-darwin.
_ARCH_SUFFIX = re.compile(
    r"\."
    r"(?:"
    r"windows|win32|universal-darwin|"
    r"(?:x86_64|i386|amd64|aarch64|armhf|arm64|sparc|powerpc|mips|s390x)"
    r"-"
    r"[A-Za-z0-9_]+"  # linux, linuxmusl, darwin, darwinlegacy, freebsd, ...
    r")$"
)


@dataclass
class TLPackage:
    name: str
    category: str = "Package"
    revision: int = 0
    shortdesc: str = ""
    longdesc: str = ""
    catalogue: str | None = None
    catalogue_version: str | None = None
    catalogue_license: str | None = None
    catalogue_ctan: str | None = None
    depends: list[str] = field(default_factory=list)
    executes: list[str] = field(default_factory=list)
    runfiles: list[str] = field(default_factory=list)
    docfiles: list[str] = field(default_factory=list)
    srcfiles: list[str] = field(default_factory=list)
    binfiles: dict[str, list[str]] = field(default_factory=dict)
    containersize: int | None = None
    doccontainersize: int | None = None
    srccontainersize: int | None = None
    relocated: bool = False

    @property
    def is_collection(self) -> bool:
        return self.name.startswith("collection-")

    @property
    def is_scheme(self) -> bool:
        return self.name.startswith("scheme-")

    @property
    def is_meta(self) -> bool:
        return self.is_collection or self.is_scheme

    @property
    def is_arch_package(self) -> bool:
        if _ARCH_SUFFIX.search(self.name):
            return True
        # Fallback: any suffix with a hyphen is an arch triple (not .doc/.source)
        if "." not in self.name:
            return False
        suffix = self.name.rsplit(".", 1)[-1]
        if suffix in ("doc", "source"):
            return False
        return "-" in suffix

    @property
    def is_doc_or_src_split(self) -> bool:
        return self.name.endswith(".doc") or self.name.endswith(".source")

    @property
    def has_doc_container(self) -> bool:
        return self.doccontainersize is not None and self.doccontainersize > 0

    @property
    def has_src_container(self) -> bool:
        return self.srccontainersize is not None and self.srccontainersize > 0

    @property
    def has_binfiles(self) -> bool:
        return bool(self.binfiles)

    def rpm_name(self) -> str:
        return f"texlive-{self.name}"

    @property
    def platform_suffix(self) -> str | None:
        """If this is a platform package (name.ARCH), return ARCH; else None."""
        if not self.is_arch_package:
            return None
        return self.name.rsplit(".", 1)[-1]

    @property
    def base_name(self) -> str:
        """TL name without platform suffix (jadetex.x86_64-linux → jadetex)."""
        if self.is_arch_package:
            return self.name.rsplit(".", 1)[0]
        return self.name

    def rpm_bin_name(self) -> str:
        """RPM name for the OM binary companion: texlive-<base>.bin."""
        return f"texlive-{self.base_name}.bin"


# TeX Live platform triples we map onto OpenMandriva arches.
# znver1 uses the same upstream x86_64-linux binaries as x86_64.
OM_TL_PLATFORMS: dict[str, str] = {
    "x86_64": "x86_64-linux",
    "znver1": "x86_64-linux",
    "aarch64": "aarch64-linux",
}
# Unique TL platforms we need as SourceN tarballs for multi-arch ABF builds.
TL_PLATFORMS_FOR_BIN: tuple[str, ...] = ("x86_64-linux", "aarch64-linux")


def platform_pkg_name(base: str, tl_platform: str) -> str:
    return f"{base}.{tl_platform}"


def bin_base_names(packages: dict[str, TLPackage]) -> list[str]:
    """Bases that have at least one linux platform package we care about."""
    bases: set[str] = set()
    for plat in TL_PLATFORMS_FOR_BIN:
        suffix = f".{plat}"
        for name, pkg in packages.items():
            if name.endswith(suffix) and pkg.has_binfiles:
                bases.add(name[: -len(suffix)])
    return sorted(bases)


def platform_packages_for_base(
    packages: dict[str, TLPackage], base: str
) -> dict[str, TLPackage]:
    """Map TL platform → package for a given base name."""
    out: dict[str, TLPackage] = {}
    for plat in TL_PLATFORMS_FOR_BIN:
        p = packages.get(platform_pkg_name(base, plat))
        if p is not None and p.has_binfiles:
            out[plat] = p
    return out


def platform_archive_filename(platform_pkg_name: str) -> str:
    """TL ships platform containers as ``name.arch.tar.xz`` (no .rN)."""
    return f"{platform_pkg_name}.tar.xz"


def platform_archive_url(mirror: str, platform_pkg_name: str) -> str:
    return f"{mirror.rstrip('/')}/{ARCHIVE}/{platform_archive_filename(platform_pkg_name)}"


def _parse_block(block: str) -> TLPackage | None:
    lines = [ln for ln in block.splitlines() if ln.strip() != ""]
    if not lines or not lines[0].startswith("name "):
        return None
    pkg = TLPackage(name=lines[0].split(" ", 1)[1].strip())
    longdesc_parts: list[str] = []
    current_list: list[str] | None = None
    current_bin_arch: str | None = None

    for ln in lines[1:]:
        if ln.startswith(" "):
            # continuation of file list
            path = ln.strip().split(" ", 1)[0]
            if current_list is not None:
                current_list.append(path)
            elif current_bin_arch is not None:
                pkg.binfiles.setdefault(current_bin_arch, []).append(path)
            continue

        current_list = None
        current_bin_arch = None
        if " " not in ln:
            key, val = ln, ""
        else:
            key, val = ln.split(" ", 1)

        if key == "category":
            pkg.category = val
        elif key == "revision":
            try:
                pkg.revision = int(val)
            except ValueError:
                pkg.revision = 0
        elif key == "shortdesc":
            pkg.shortdesc = val
        elif key == "longdesc":
            longdesc_parts.append(val)
        elif key == "depend":
            pkg.depends.append(val)
        elif key == "execute":
            pkg.executes.append(val)
        elif key == "catalogue":
            pkg.catalogue = val
        elif key == "catalogue-version":
            pkg.catalogue_version = val
        elif key == "catalogue-license":
            pkg.catalogue_license = val
        elif key == "catalogue-ctan":
            pkg.catalogue_ctan = val
        elif key == "containersize":
            try:
                pkg.containersize = int(val)
            except ValueError:
                pass
        elif key == "doccontainersize":
            try:
                pkg.doccontainersize = int(val)
            except ValueError:
                pass
        elif key == "srccontainersize":
            try:
                pkg.srccontainersize = int(val)
            except ValueError:
                pass
        elif key == "relocated":
            pkg.relocated = val in ("1", "true", "yes")
        elif key == "runfiles":
            current_list = pkg.runfiles
        elif key == "docfiles":
            current_list = pkg.docfiles
        elif key == "srcfiles":
            current_list = pkg.srcfiles
        elif key == "binfiles":
            # binfiles arch=x86_64-linux size=...
            m = re.search(r"arch=(\S+)", val)
            if m:
                current_bin_arch = m.group(1)
                pkg.binfiles.setdefault(current_bin_arch, [])
        # ignore checksums, catalogue-topics, etc.

    pkg.longdesc = "\n".join(longdesc_parts)
    return pkg


def parse_tlpdb(text: str) -> dict[str, TLPackage]:
    packages: dict[str, TLPackage] = {}
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        pkg = _parse_block(block)
        if pkg is None:
            continue
        packages[pkg.name] = pkg
    return packages


def load_tlpdb(path: Path) -> dict[str, TLPackage]:
    data = path.read_bytes()
    if path.suffix == ".xz" or data[:6] == b"\xfd7zXZ\x00":
        text = lzma.decompress(data).decode("utf-8", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    return parse_tlpdb(text)


def fetch_tlpdb(
    dest: Path,
    mirror: str = DEFAULT_MIRROR,
    timeout: int = 120,
) -> Path:
    """Download texlive.tlpdb.xz to dest (file path). Returns path to plain tlpdb."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{mirror.rstrip('/')}/{TLPDB_XZ}"
    req = urllib.request.Request(url, headers={"User-Agent": "OpenMandriva-texlive-sync/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    xz_path = dest if dest.suffix == ".xz" else dest.with_suffix(dest.suffix + ".xz")
    if dest.suffix != ".xz":
        xz_path = Path(str(dest) + ".xz")
    xz_path.write_bytes(raw)
    plain = dest if dest.suffix != ".xz" else dest.with_suffix("")
    if plain == xz_path:
        plain = dest.parent / "texlive.tlpdb"
    plain.write_bytes(lzma.decompress(raw))
    return plain


def archive_url(mirror: str, name: str, revision: int, kind: str = "run") -> str:
    """kind: run | doc | source"""
    base = f"{mirror.rstrip('/')}/{ARCHIVE}"
    if kind == "run":
        # Platform packages (name.x86_64-linux) use unrevisioned archive names.
        if _ARCH_SUFFIX.search(name) or (
            "." in name and "-" in name.rsplit(".", 1)[-1]
            and not name.endswith((".doc", ".source"))
        ):
            return f"{base}/{name}.tar.xz"
        return f"{base}/{name}.r{revision}.tar.xz"
    if kind == "doc":
        return f"{base}/{name}.doc.r{revision}.tar.xz"
    if kind == "source":
        return f"{base}/{name}.source.r{revision}.tar.xz"
    raise ValueError(kind)


def archive_filename(name: str, revision: int, kind: str = "run") -> str:
    if kind == "run":
        # Platform packages (name.x86_64-linux) use unrevisioned archive names.
        if _ARCH_SUFFIX.search(name) or (
            "." in name and "-" in name.rsplit(".", 1)[-1]
            and not name.endswith((".doc", ".source"))
        ):
            return f"{name}.tar.xz"
        return f"{name}.r{revision}.tar.xz"
    if kind == "doc":
        return f"{name}.doc.r{revision}.tar.xz"
    if kind == "source":
        return f"{name}.source.r{revision}.tar.xz"
    raise ValueError(kind)


def is_skippable_depend(dep: str) -> bool:
    return bool(_SKIP_DEPEND.match(dep))


def iter_packagable(packages: dict[str, TLPackage]) -> Iterable[TLPackage]:
    """Yield packages we generate RPMs for (no arch splits, no .doc/.source)."""
    for name, pkg in sorted(packages.items()):
        if pkg.is_arch_package:
            continue
        if pkg.is_doc_or_src_split:
            continue
        if name.startswith("00texlive"):
            continue
        if pkg.category not in ("Package", "Collection", "Scheme", "TLCore", "ConTeXt"):
            # Still allow unknown categories that look like packages
            if pkg.category not in ("Package", "Collection", "Scheme"):
                if not pkg.revision:
                    continue
        yield pkg
