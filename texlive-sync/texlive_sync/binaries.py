"""Inspect TeX Live platform packages and model OM-safe .bin companions.

OpenMandriva policy: never ship prebuilt native binaries that were not built
from source. Upstream TL ``name.x86_64-linux`` archives often contain ELFs; we
must not use those as RPM Sources.

What we *can* auto-generate are noarch wrapper packages that only install
symlinks discovered from a platform archive (used as *metadata*, never as an
RPM source):

* engine format wrappers: ``jadetex -> pdftex``
* script wrappers: ``epstopdf -> ../../texmf-dist/scripts/epstopdf/epstopdf.pl``
* alias links: ``repstopdf -> epstopdf``

Bases whose platform tree contains real ELFs (or other non-symlink payloads)
are classified as ``native`` and skipped by the auto generator — they need a
proper multi-arch source build (web2c, kpathsea, biber, …).
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from .tlpdb import (
    DEFAULT_MIRROR,
    TLPackage,
    platform_archive_filename,
    platform_archive_url,
    platform_packages_for_base,
)

# Preferred platform archive to inspect for symlink metadata (same links on
# every Unix platform for wrapper-only packages).
_INSPECT_PLATFORM = "x86_64-linux"

BinKind = Literal["wrapper", "native", "empty", "unknown"]
EntryKind = Literal["link", "elf", "script", "file", "other"]


@dataclass(frozen=True)
class BinEntry:
    """One bindir entry from a platform tree."""

    name: str
    kind: EntryKind
    target: str | None = None  # symlink target as stored upstream


@dataclass
class BinAnalysis:
    base: str
    kind: BinKind
    entries: list[BinEntry] = field(default_factory=list)
    revision: int = 0
    error: str | None = None

    @property
    def links(self) -> list[BinEntry]:
        return [e for e in self.entries if e.kind == "link"]


def _classify_file_magic(head: bytes) -> EntryKind:
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"#!"):
        return "script"
    if head[:2] in (b"MZ", b"\xfe\xed"):  # PE / Mach-O fat
        return "elf"
    return "file"


def inspect_platform_tar(data: bytes) -> list[BinEntry]:
    """Parse a TL platform ``.tar.xz`` and list bindir entries."""
    entries: list[BinEntry] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
        for m in tf.getmembers():
            # bin/<arch>/name
            parts = m.name.split("/")
            if len(parts) < 3 or parts[0] != "bin" or m.isdir():
                continue
            name = parts[-1]
            if not name or name.startswith("."):
                continue
            if m.issym() or m.islnk():
                entries.append(BinEntry(name=name, kind="link", target=m.linkname))
            elif m.isfile():
                fh = tf.extractfile(m)
                head = fh.read(4) if fh is not None else b""
                entries.append(
                    BinEntry(name=name, kind=_classify_file_magic(head), target=None)
                )
            else:
                entries.append(BinEntry(name=name, kind="other", target=None))
    # stable order
    entries.sort(key=lambda e: e.name)
    return entries


def classify_entries(entries: Iterable[BinEntry]) -> BinKind:
    ents = list(entries)
    if not ents:
        return "empty"
    if any(e.kind == "elf" for e in ents):
        return "native"
    # Non-symlink payload in bindir (embedded scripts, blobs) — not auto-safe
    # without a source story; treat as native/skip.
    if any(e.kind != "link" for e in ents):
        return "native"
    return "wrapper"


def download_platform_archive(
    base: str,
    dest: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    platform: str = _INSPECT_PLATFORM,
) -> Path:
    """Download platform archive for *inspection only* (not RPM Source)."""
    dest.mkdir(parents=True, exist_ok=True)
    full = f"{base}.{platform}"
    fn = platform_archive_filename(full)
    path = dest / fn
    if path.is_file() and path.stat().st_size > 0:
        return path
    # Reuse apply's multi-mirror download (CTAN geo-mirrors lag).
    from .apply import download_url_with_fallback

    rel = f"archive/{fn}"
    return download_url_with_fallback(
        rel, path, mirror=mirror, timeout=300
    )


def analyze_bin_base(
    base: str,
    packages: dict[str, TLPackage],
    cache: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    meta_cache: Path | None = None,
) -> BinAnalysis:
    """
    Classify a bin base and collect symlink entries.

    Uses the x86_64-linux platform tarball purely as metadata. Never treated
    as an RPM source by the generator/apply path.
    """
    plat_pkgs = platform_packages_for_base(packages, base)
    rev = max((p.revision for p in plat_pkgs.values()), default=0)

    meta_dir = meta_cache or (cache / "_bin_meta")
    meta_path = meta_dir / f"{base}.json"
    if meta_path.is_file():
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            if raw.get("revision") == rev and raw.get("base") == base:
                entries = [BinEntry(**e) for e in raw.get("entries") or []]
                return BinAnalysis(
                    base=base,
                    kind=raw.get("kind") or classify_entries(entries),
                    entries=entries,
                    revision=rev,
                )
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            pass

    if not plat_pkgs:
        return BinAnalysis(base=base, kind="empty", revision=rev, error="no platform packages")

    try:
        path = download_platform_archive(base, cache, mirror=mirror)
        data = path.read_bytes()
        entries = inspect_platform_tar(data)
    except Exception as exc:  # noqa: BLE001
        return BinAnalysis(
            base=base, kind="unknown", revision=rev, error=str(exc)
        )

    kind = classify_entries(entries)
    analysis = BinAnalysis(base=base, kind=kind, entries=entries, revision=rev)

    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "base": base,
                "revision": rev,
                "kind": kind,
                "entries": [asdict(e) for e in entries],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return analysis


def wrapper_bindir_target(link_target: str) -> str:
    """
    Map an upstream symlink target to an RPM bindir install target.

    * ``pdftex`` → ``pdftex`` (sibling in %{_bindir})
    * ``../../texmf-dist/scripts/foo/bar.pl`` → ``%{_texmfdistdir}/scripts/foo/bar.pl``
    * absolute paths kept as-is
    """
    t = link_target.strip()
    if not t:
        raise ValueError("empty symlink target")
    if t.startswith("/"):
        return t
    # Normal TL script wrapper
    marker = "texmf-dist/"
    if marker in t:
        rest = t.split(marker, 1)[1]
        return f"%{{_texmfdistdir}}/{rest}"
    # Also accept texmf/ (rare)
    marker2 = "texmf/"
    if marker2 in t and "texmf-dist/" not in t:
        rest = t.split(marker2, 1)[1]
        return f"%{{_texmfdir}}/{rest}"
    # Sibling name (engine or another wrapper)
    if "/" in t or t.startswith("."):
        raise ValueError(f"unsupported symlink target: {link_target!r}")
    return t


def wrapper_requires(base: str, entries: list[BinEntry]) -> list[str]:
    """Minimal Requires for a wrapper-only .bin package."""
    reqs: list[str] = []
    seen: set[str] = set()
    needs_main = False
    # Only foldable links are installed; same set for Requires.
    foldable = [e for e in entries if is_foldable_wrapper_link(e)]
    # Names this package itself installs — sibling aliases must not
    # Require themselves (repstopdf -> epstopdf in the same .bin).
    provided = {e.name for e in foldable}
    for e in foldable:
        assert e.target is not None
        t = e.target
        if "texmf-dist/" in t or (
            "texmf/" in t and "texmf-dist/" not in t
        ):
            needs_main = True
            continue
        if t.startswith("/"):
            cap = t  # file dep
        elif "/" in t or t.startswith("."):
            continue
        elif t in provided:
            # Alias to another wrapper in this same package.
            continue
        else:
            # Sibling engine/wrapper binary must exist on the system.
            cap = f"/usr/bin/{t}"
        if cap not in seen:
            seen.add(cap)
            reqs.append(cap)
    if needs_main:
        cap = f"texlive({base})"
        if cap not in seen:
            reqs.insert(0, cap)
    return reqs


def auto_bin_bases(
    packages: dict[str, TLPackage],
    cache: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    only: list[str] | None = None,
) -> tuple[list[str], dict[str, BinAnalysis]]:
    """
    Return bases eligible for auto wrapper .bin generation, plus full analysis map.

    *only* restricts which bases to analyze; default is all bin bases with
    linux platform packages.
    """
    from .tlpdb import bin_base_names

    bases = list(only) if only is not None else bin_base_names(packages)
    analyses: dict[str, BinAnalysis] = {}
    wrappers: list[str] = []
    for base in bases:
        a = analyze_bin_base(base, packages, cache, mirror=mirror)
        analyses[base] = a
        if a.kind == "wrapper":
            wrappers.append(base)
    return wrappers, analyses


# ---------------------------------------------------------------------------
# Source-build model (monorepo TeX Live sources)
# ---------------------------------------------------------------------------

# TL packages whose platform trees contain real code, but that are *not*
# produced by the default ``./Build`` of texlive-*-source (or need a
# separate recipe). Auto texlive-bin skips these Provides unless enabled.
EXTERNAL_NATIVE_BASES: frozenset[str] = frozenset(
    {
        "asymptote",  # utils/asymptote, disabled by default; heavy deps
        "biber",  # Par::Packer fat binary; not in TL Build
        "biber-ms",
        "context",  # luametatex via cmake / separate packaging
        "xindy",  # needs clisp; disabled by default
        "luajittex",  # optional JIT; arch/CPU constrained
        "mflua",  # mfluajit pieces often tied to luajit
    }
)

# Rough map TL package name → source tree path (for docs / future splits).
# Engines under web2c are not 1:1 with directories.
SOURCE_TREE_HINTS: dict[str, str] = {
    "afm2pl": "texk/afm2pl",
    "bibtex": "texk/web2c",
    "bibtex8": "texk/bibtex-x",
    "bibtexu": "texk/bibtex-x",
    "chktex": "texk/chktex",
    "cjkutils": "texk/cjkutils",
    "ctie": "texk/web2c",
    "cweb": "texk/web2c",
    "detex": "texk/detex",
    "dtl": "texk/dtl",
    "dvi2tty": "texk/dvi2tty",
    "dvicopy": "texk/web2c",
    "dvidvi": "texk/dvidvi",
    "dviljk": "texk/dviljk",
    "dviout-util": "texk/dviout-util",
    "dvipdfmx": "texk/dvipdfm-x",
    "dvipng": "texk/dvipng",
    "dvipos": "texk/dvipos",
    "dvips": "texk/dvipsk",
    "dvisvgm": "texk/dvisvgm",
    "fontware": "texk/web2c",
    "gregoriotex": "texk/gregorio",
    "gsftopk": "texk/gsftopk",
    "hitex": "texk/web2c",
    "kpathsea": "texk/kpathsea",
    "lacheck": "utils/lacheck",
    "lcdftypetools": "texk/lcdf-typetools",
    "luahbtex": "texk/web2c",
    "luatex": "texk/web2c",
    "makeindex": "texk/makeindexk",
    "metafont": "texk/web2c",
    "metapost": "texk/web2c",
    "mfware": "texk/web2c",
    "omegaware": "texk/web2c",
    "patgen": "texk/web2c",
    "pdftex": "texk/web2c",
    "pdftosrc": "texk/web2c",
    "ps2pk": "texk/ps2pk",
    "psutils": "texk/psutils",
    "ptex": "texk/web2c",
    "seetexk": "texk/seetexk",
    "synctex": "texk/web2c",
    "t1utils": "utils/t1utils",
    "tex": "texk/web2c",
    "tex4ht": "texk/tex4htk",
    "texware": "texk/web2c",
    "tie": "texk/web2c",
    "ttfutils": "texk/ttf2pk2",
    "upmendex": "texk/upmendex",
    "uptex": "texk/web2c",
    "velthuis": "utils/devnag",
    "vlna": "utils/vlna",
    "web": "texk/web2c",
    "xdvi": "texk/xdvik",
    "xdvipsk": "texk/xdvipsk",
    "xetex": "texk/web2c",
    "autosp": "utils/autosp",
    "axodraw2": "utils/axodraw2",
    "m-tx": "utils/m-tx",
    "pmx": "utils/pmx",
    "ps2eps": "utils/ps2eps",
    "tpic2pdftex": "utils/tpic2pdftex",
    "xml2pmx": "utils/xml2pmx",
    "xpdfopen": "utils/xpdfopen",
    "musixtnt": "texk/musixtnt",
}


def is_external_native(base: str) -> bool:
    return base in EXTERNAL_NATIVE_BASES


# Never install these as %{_bindir}/NAME — they collide with core system tools
# or are TL install-tree conveniences, not commands.
_FORBIDDEN_BINDIR_NAMES = frozenset(
    {
        "man",  # TL links this to texmf-dist/doc/man (manpage tree), not a binary
        "sh",
        "bash",
        "cat",
        "ls",
        "cp",
        "mv",
        "rm",
        "true",
        "false",
        "test",
        "echo",
        "pwd",
        "env",
        "sed",
        "awk",
        "grep",
        "find",
        "make",
        "perl",
        "python",
        "python3",
    }
)


def is_foldable_wrapper_link(entry: BinEntry) -> bool:
    """
    True if this bindir symlink is safe to install under %{_bindir}.

    Upstream TL sometimes puts convenience links in bin/ that are not commands
    (notably ``man -> ../../texmf-dist/doc/man``). Folding those would
    clobber system tools.
    """
    if entry.kind != "link" or not entry.target:
        return False
    if entry.name in _FORBIDDEN_BINDIR_NAMES:
        return False
    t = entry.target.replace("\\", "/")
    # Doc/info trees and bare manpage directories are not executables.
    if "/doc/" in t or "/source/" in t:
        return False
    if t.rstrip("/").endswith("/man") or t.rstrip("/").endswith("/info"):
        return False
    return True


def wrapper_link_pairs(entries: list[BinEntry]) -> list[tuple[str, str]]:
    """(bindir_name, install_target) for foldable wrapper-only links."""
    out: list[tuple[str, str]] = []
    for e in entries:
        if not is_foldable_wrapper_link(e):
            continue
        assert e.target is not None
        out.append((e.name, wrapper_bindir_target(e.target)))
    return out


# Extra bindir links that are not present (or not complete) in a platform
# companion but whose script payload ships in the noarch module. Keyed by
# TL package name.
#
# texlive-scripts: texhash -> mktexlsr, but mktexlsr itself lives in blocked
# texlive.infra upstream; the .pl is in this package's runfiles.
_EXTRA_BIN_LINKS: dict[str, list[tuple[str, str]]] = {
    "texlive-scripts": [
        (
            "mktexlsr",
            "%{_texmfdistdir}/scripts/texlive/mktexlsr.pl",
        ),
    ],
}


def extra_bin_link_pairs(tl_name: str) -> list[tuple[str, str]]:
    """Additional (name, target) pairs to fold for *tl_name*."""
    return list(_EXTRA_BIN_LINKS.get(tl_name) or [])


def all_bin_link_pairs(
    entries: list[BinEntry], *, tl_name: str | None = None
) -> list[tuple[str, str]]:
    """Foldable platform links plus package-specific extras (no name clashes)."""
    pairs = wrapper_link_pairs(entries)
    if not tl_name:
        return pairs
    seen = {n for n, _ in pairs}
    for name, target in extra_bin_link_pairs(tl_name):
        if name not in seen:
            pairs.append((name, target))
            seen.add(name)
    return pairs


def tl_bin_links_global(pairs: list[tuple[str, str]]) -> str:
    """Value for ``%global tl_bin_links`` (space-separated name:target)."""
    parts = []
    for name, target in pairs:
        if " " in name or " " in target:
            raise ValueError(f"refusing space in bin link {name!r} -> {target!r}")
        parts.append(f"{name}:{target}")
    return " ".join(parts)


def core_native_bases(analyses: dict[str, BinAnalysis]) -> list[str]:
    """Native bases that belong in the monorepo texlive-bin build."""
    out = []
    for base, a in sorted(analyses.items()):
        if a.kind != "native":
            continue
        if is_external_native(base):
            continue
        out.append(base)
    return out


def default_source_version(quirks: dict[str, Any] | None = None) -> str:
    """Pinned TeX Live source tarball date (YYYYMMDD) from quirks or default."""
    qs = (quirks or {}).get("texlive_source") or {}
    return str(qs.get("version") or "20260301")


def source_tarball_url(version: str, mirror_root: str | None = None) -> str:
    root = (mirror_root or "https://mirrors.ctan.org/systems/texlive/Source").rstrip(
        "/"
    )
    return f"{root}/texlive-{version}-source.tar.xz"


def source_tarball_filename(version: str) -> str:
    return f"texlive-{version}-source.tar.xz"
