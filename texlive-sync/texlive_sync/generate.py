"""Generate OpenMandriva RPM specs from TLPackage metadata."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any

from .deps import rpm_requires
from .binaries import (
    BinAnalysis,
    analyze_bin_base,
    core_native_bases,
    default_source_version,
    is_external_native,
    source_tarball_filename,
    source_tarball_url,
    tl_bin_links_global,
    wrapper_bindir_target,
    wrapper_link_pairs,
    wrapper_requires,
)
from .tlpdb import (
    DEFAULT_MIRROR,
    TLPackage,
    archive_filename,
    platform_packages_for_base,
)
from .versioning import rpm_version_release


def _wrap_desc(text: str, width: int = 72) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return "TeX Live package."
    return "\n".join(textwrap.wrap(text, width=width)) or "TeX Live package."


def _license(pkg: TLPackage) -> str:
    lic = (pkg.catalogue_license or "").strip()
    return lic if lic else "LPPL"


def _url(pkg: TLPackage) -> str:
    if pkg.catalogue_ctan:
        path = pkg.catalogue_ctan.lstrip("/")
        return f"https://www.ctan.org/tex-archive/{path}"
    return f"https://www.ctan.org/pkg/{pkg.catalogue or pkg.name}"


def resolve_wrapper_bin_analysis(
    pkg: TLPackage,
    packages: dict[str, TLPackage],
    *,
    cache: Path | None = None,
    mirror: str = DEFAULT_MIRROR,
) -> BinAnalysis | None:
    """
    If ``pkg`` has a platform companion that is wrapper-only, return its
    analysis so bindir links + ``texlive(name.bin)`` can be folded into the
    noarch module. Natives return None (satisfied by monorepo texlive-binaries).
    """
    if not platform_packages_for_base(packages, pkg.name):
        return None
    cache = cache or Path("/tmp/texlive-bin-cache")
    cache.mkdir(parents=True, exist_ok=True)
    analysis = analyze_bin_base(pkg.name, packages, cache, mirror=mirror)
    if analysis.kind != "wrapper":
        return None
    return analysis


def generate_spec(
    pkg: TLPackage,
    quirks: dict[str, Any],
    mirror: str = DEFAULT_MIRROR,
    packrel: int = 1,
    bin_analysis: BinAnalysis | None = None,
    packages: dict[str, TLPackage] | None = None,
    bin_cache: Path | None = None,
) -> str:
    """
    Generate a slim declarative spec using BuildSystem: texlive.

    Prep/install/%files logic lives in macros.buildsys.texlive (texlive-tlpkg).
    Requires rpm that allows BuildSystem macros to come from BuildRequires
    (see rpm-6.0-buildsystem-defer-unknown.patch).

    When ``bin_analysis`` is omitted but ``packages`` is provided, wrapper-only
    platform companions are auto-detected and folded (tl_bin_links + Provides
    texlive(name.bin)). Mass apply must pass ``packages`` or wrappers ship
    Requires: texlive(name.bin) with nothing providing it.
    """
    if bin_analysis is None and packages is not None:
        bin_analysis = resolve_wrapper_bin_analysis(
            pkg, packages, cache=bin_cache, mirror=mirror
        )
    version_num, _release_num = rpm_version_release(
        pkg.revision, pkg.catalogue_version, packrel=packrel
    )
    if pkg.catalogue_version and version_num != str(pkg.revision):
        version_tag = version_num
        release_tag = f"%{{tl_revision}}.{packrel}"
    else:
        version_tag = "%{tl_revision}"
        release_tag = str(packrel)

    # Catalogue versions are typically << old OM Version=revision; bump Epoch
    epoch = (quirks.get("epoch") or {}).get(pkg.name)
    if epoch is None and pkg.catalogue_version and version_num != str(pkg.revision):
        epoch = 1
    requires = rpm_requires(pkg, quirks)

    base = f"{mirror.rstrip('/')}/archive"
    rev = "%{tl_revision}"
    sources: list[tuple[int, str]] = []
    sources.append((0, f"{base}/{pkg.name}.r{rev}.tar.xz"))
    src_idx = 1
    if pkg.has_doc_container:
        sources.append((src_idx, f"{base}/{pkg.name}.doc.r{rev}.tar.xz"))
        src_idx += 1
    if pkg.has_src_container:
        sources.append((src_idx, f"{base}/{pkg.name}.source.r{rev}.tar.xz"))
        src_idx += 1

    summary = (pkg.shortdesc or f"TeX Live package {pkg.name}").replace("\n", " ").strip()
    summary = summary.replace('"', "").replace("'", "")
    if len(summary) > 80:
        summary = summary[:77] + "..."

    # Fold pure script/engine wrappers into the noarch module: install
    # bindir symlinks via %%global tl_bin_links and Provide texlive(name.bin).
    # Real ELFs are *not* folded — they come from the monorepo texlive-bin build.
    folded_bin = False
    bin_links_global = ""
    if bin_analysis is not None and bin_analysis.kind == "wrapper":
        pairs = wrapper_link_pairs(bin_analysis.links)
        if pairs:
            bin_links_global = tl_bin_links_global(pairs)
            folded_bin = True

    lines: list[str] = [
        f"%global tl_name {pkg.name}",
        f"%global tl_revision {pkg.revision}",
    ]
    if bin_links_global:
        lines.append(f"%global tl_bin_links {bin_links_global}")
    lines += [
        "",
        f"Name:\t\ttexlive-%{{tl_name}}",
    ]
    if epoch is not None:
        lines.append(f"Epoch:\t\t{epoch}")
    lines += [
        f"Version:\t{version_tag}",
        f"Release:\t{release_tag}",
        f"Summary:\t{summary}",
        "Group:\t\tPublishing",
        f"URL:\t\t{_url(pkg)}",
        f"License:\t{_license(pkg)}",
    ]
    for idx, url in sources:
        lines.append(f"Source{idx}:\t{url}")

    lines += [
        "BuildArch:\tnoarch",
        "BuildSystem:\ttexlive",
        # Build-time: rpm injects BuildRequires: rpm-build(texlive) so the
        # macros package (texlive-tlpkg) is installed for BuildSystem expansion.
        # Runtime: no hard dep on tlpkg — filetriggers live there and fire if
        # it is installed; collections/schemes pull infrastructure as needed.
    ]
    for req in requires:
        lines.append(f"Requires:\t{req}")

    lines.append(f"Provides:\ttexlive(%{{tl_name}}) = %{{tl_revision}}")
    if folded_bin:
        lines.append(f"Provides:\ttexlive(%{{tl_name}}.bin) = %{{tl_revision}}")
        lines.append(f"Provides:\ttexlive-%{{tl_name}}.bin = %{{EVRD}}")
    for prov in (quirks.get("extra_provides") or {}).get(pkg.name, []) or []:
        lines.append(f"Provides:\t{prov}")

    lines += [
        "",
        "%description",
        _wrap_desc(pkg.longdesc or pkg.shortdesc or f"TeX Live package {pkg.name}."),
        "",
        # BuildSystem: texlive supplies %prep/%conf/%build/%install and
        # writes %files via %{specpartsdir}. No %post (filetriggers).
        "",
    ]
    return "\n".join(lines)


def generate_abf_yml_placeholder(pkg: TLPackage) -> str:
    """Placeholder .abf.yml; sha1 filled later by abf store / apply."""
    lines = ["sources:"]
    revs = pkg.revision
    files = [archive_filename(pkg.name, revs, "run")]
    if pkg.has_doc_container:
        files.append(archive_filename(pkg.name, revs, "doc"))
    if pkg.has_src_container:
        files.append(archive_filename(pkg.name, revs, "source"))
    for fn in files:
        lines.append(f"  {fn}: 0000000000000000000000000000000000000000")
    lines.append("")
    return "\n".join(lines)


def write_package(
    pkg: TLPackage,
    out_dir: Path,
    quirks: dict[str, Any],
    mirror: str = DEFAULT_MIRROR,
    bin_analysis: BinAnalysis | None = None,
) -> Path:
    """Write texlive-<name>/ spec + placeholder .abf.yml. Returns package dir."""
    pkg_dir = out_dir / f"texlive-{pkg.name}"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / f"texlive-{pkg.name}.spec").write_text(
        generate_spec(pkg, quirks, mirror=mirror, bin_analysis=bin_analysis),
        encoding="utf-8",
    )
    (pkg_dir / ".abf.yml").write_text(
        generate_abf_yml_placeholder(pkg), encoding="utf-8"
    )
    return pkg_dir


def generate_bin_spec(
    base: str,
    analysis: BinAnalysis,
    packages: dict[str, TLPackage],
    quirks: dict[str, Any],
    packrel: int = 1,
) -> str:
    """
    Generate a noarch ``texlive-<base>.bin`` wrapper package.

    OM policy: no prebuilt native binaries. This path only emits packages
    whose platform tree is entirely symlinks (engine wrappers / script
    links). Symlink targets are taken from BinAnalysis (platform archive
    used as metadata only — never as RPM Source).
    """
    if analysis.kind != "wrapper":
        raise ValueError(
            f"{base}.bin is not auto-packagable (kind={analysis.kind}); "
            f"native tools must be built from source"
        )
    links = analysis.links
    if not links:
        raise ValueError(f"{base}.bin has no symlink entries")

    rev = analysis.revision
    epoch = (quirks.get("epoch") or {}).get(base) or (quirks.get("epoch") or {}).get(
        f"{base}.bin"
    )

    # Licence/summary: prefer the noarch base package when present
    base_pkg = packages.get(base)
    sample = base_pkg or next(
        iter(platform_packages_for_base(packages, base).values()), None
    )
    lic = ((sample.catalogue_license if sample else None) or "").strip() or "LPPL"
    summary = f"Wrappers for TeX Live package {base}"
    longdesc = (
        f"Architecture-independent command wrappers for the TeX Live package "
        f"{base} (symlinks to engines and/or scripts under texmf-dist). "
        f"Does not ship prebuilt native binaries."
    )

    reqs = wrapper_requires(base, links)
    # Allow quirks to inject extra requires for awkward engines
    for extra in (quirks.get("extra_requires") or {}).get(f"{base}.bin", []) or []:
        if extra not in reqs:
            reqs.append(extra)

    lines: list[str] = [
        f"%global tl_name {base}",
        f"%global tl_revision {rev}",
        "",
        f"Name:\t\ttexlive-%{{tl_name}}.bin",
    ]
    if epoch is not None:
        lines.append(f"Epoch:\t\t{epoch}")
    lines += [
        f"Version:\t%{{tl_revision}}",
        f"Release:\t{packrel}",
        f"Summary:\t{summary}",
        "Group:\t\tPublishing",
        "URL:\t\thttps://tug.org/texlive",
        f"License:\t{lic}",
        # No Source*: wrappers are generated; platform tarballs are never shipped.
        "BuildArch:\tnoarch",
        f"Provides:\ttexlive(%{{tl_name}}.bin) = %{{tl_revision}}",
        f"Provides:\ttexlive-%{{tl_name}}.bin = %{{EVRD}}",
    ]
    for r in reqs:
        lines.append(f"Requires:\t{r}")

    lines += [
        "",
        "%description",
        _wrap_desc(longdesc),
        "",
        "%prep",
        "# No sources — install only creates symlinks (see %install).",
        "",
        "%install",
        "mkdir -p %{buildroot}%{_bindir}",
    ]
    file_list: list[str] = []
    for e in links:
        assert e.target is not None
        dest = wrapper_bindir_target(e.target)
        # Explicit link; names are from tlpdb (safe path segments).
        lines.append(
            f'ln -sfn {dest} %{{buildroot}}%{{_bindir}}/{e.name}'
        )
        file_list.append(f"%{{_bindir}}/{e.name}")

    lines += [
        "",
        "%files",
        *file_list,
        "",
    ]
    return "\n".join(lines)


def generate_bin_abf_yml_placeholder() -> str:
    """Wrapper .bin packages have no source archives."""
    return "sources: {}\n"


def write_bin_package(
    base: str,
    packages: dict[str, TLPackage],
    out_dir: Path,
    quirks: dict[str, Any],
    *,
    mirror: str = DEFAULT_MIRROR,
    cache: Path | None = None,
    analysis: BinAnalysis | None = None,
) -> Path:
    """Write texlive-<base>.bin/ wrapper spec + empty .abf.yml."""
    cache = cache or (out_dir / "_bin_cache")
    if analysis is None:
        analysis = analyze_bin_base(base, packages, cache, mirror=mirror)
    if analysis.kind != "wrapper":
        raise ValueError(
            f"cannot write bin package for {base}: kind={analysis.kind}"
            + (f" ({analysis.error})" if analysis.error else "")
        )
    pkg_dir = out_dir / f"texlive-{base}.bin"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / f"texlive-{base}.bin.spec").write_text(
        generate_bin_spec(base, analysis, packages, quirks), encoding="utf-8"
    )
    (pkg_dir / ".abf.yml").write_text(
        generate_bin_abf_yml_placeholder(), encoding="utf-8"
    )
    return pkg_dir


def generate_texlive_bin_spec(
    packages: dict[str, TLPackage],
    analyses: dict[str, BinAnalysis],
    quirks: dict[str, Any],
    *,
    packrel: int | None = None,
) -> str:
    """
    Generate the monorepo ``texlive-bin`` SRPM spec.

    Source is CTAN ``texlive-YYYYMMDD-source.tar.xz``. One arch build produces
    ``texlive-binaries`` with ``Provides: texlive(name.bin)`` for every core
    native TL package (engines/tools built by upstream ``./Build``).

    External natives (biber, asymptote, xindy, context, luajit, …) are omitted.
    """
    qs = quirks.get("texlive_source") or {}
    version = default_source_version(quirks)
    packrel = int(packrel if packrel is not None else qs.get("packrel") or 1)
    url = qs.get("url") or source_tarball_url(version)
    system = set((quirks.get("system_packages") or {}).keys())
    cores = [c for c in core_native_bases(analyses) if c not in system]

    provides_lines: list[str] = []
    for base in cores:
        a = analyses[base]
        rev = a.revision or getattr(packages.get(base), "revision", 0) or 0
        ver = rev if rev else version
        provides_lines.append(f"Provides:\ttexlive({base}.bin) = {ver}")
        provides_lines.append(f"Provides:\ttexlive-{base}.bin = %{{EVRD}}")

    desc = (
        "Architecture-specific TeX Live engines and tools built from the "
        "official TeX Live source monorepo (not from prebuilt "
        "name.x86_64-linux archives). Provides texlive(name.bin) for noarch "
        "module dependencies."
    )
    core_note = ", ".join(cores[:24]) + ("…" if len(cores) > 24 else "")

    configure_args = [
        "--disable-native-texlive-build",
        "--with-banner-add=/OpenMandriva",
        "--disable-xindy",
        "--disable-xindy-rules",
        "--disable-xindy-docs",
        "--with-system-zlib",
        "--with-system-libpng",
        "--with-system-freetype2",
        "--with-system-harfbuzz",
        "--with-system-icu",
        "--with-system-gmp",
        "--with-system-mpfr",
        "--with-system-poppler",
        "--with-system-cairo",
        "--with-system-pixman",
        "--with-system-graphite2",
        "--prefix=%{_prefix}",
        "--bindir=%{_bindir}",
        "--datarootdir=%{_datadir}",
        "--libdir=%{_libdir}",
        "--includedir=%{_includedir}",
        "--mandir=%{_mandir}",
        "--infodir=%{_infodir}",
    ]
    for extra in qs.get("configure_args") or []:
        configure_args.append(str(extra))

    cfg = " \\\n\t".join(configure_args)

    lines = [
        f"%global tl_source_version {version}",
        "",
        "Name:\t\ttexlive-bin",
        "Version:\t%{tl_source_version}",
        f"Release:\t{packrel}",
        "Summary:\tTeX Live engines and tools (source monorepo)",
        "Group:\t\tPublishing",
        "URL:\t\thttps://tug.org/texlive",
        "License:\tGPLv2+ and LGPLv2+ and GPLv3+ and Public Domain and MIT and BSD and LPPL",
        f"Source0:\t{url}",
        "",
        "BuildRequires:\tgcc",
        "BuildRequires:\tgcc-c++",
        "BuildRequires:\tmake",
        "BuildRequires:\tbison",
        "BuildRequires:\tflex",
        "BuildRequires:\tpkgconfig(zlib)",
        "BuildRequires:\tpkgconfig(libpng)",
        "BuildRequires:\tpkgconfig(freetype2)",
        "BuildRequires:\tpkgconfig(harfbuzz)",
        "BuildRequires:\tpkgconfig(icu-uc)",
        "BuildRequires:\tpkgconfig(gmp)",
        "BuildRequires:\tpkgconfig(mpfr)",
        "BuildRequires:\tpkgconfig(poppler)",
        "BuildRequires:\tpkgconfig(cairo)",
        "BuildRequires:\tpkgconfig(pixman-1)",
        "BuildRequires:\tpkgconfig(graphite2)",
        "",
        "%description",
        _wrap_desc(
            desc
            + f" This SRPM builds the shared libraries and the texlive-binaries "
            f"package covering: {core_note}."
        ),
        "",
        "%package -n texlive-binaries",
        "Summary:\tTeX Live engines and tools (built from source)",
        "Group:\t\tPublishing",
        *provides_lines,
        "",
        "%description -n texlive-binaries",
        _wrap_desc(desc + f" Includes: {core_note}."),
        "",
        "%prep",
        "%autosetup -n texlive-%{tl_source_version}-source",
        "",
        "%build",
        "mkdir -p Work",
        "cd Work",
        f"../configure \\\n\t{cfg}",
        # Use %%make_build from the Work dir
        "cd ..",
        "%make_build -C Work",
        "",
        "%install",
        "%make_install -C Work",
        "# Modules ship texmf noarch; drop any data trees from this arch package.",
        "rm -rf %{buildroot}%{_datadir}/texmf %{buildroot}%{_datadir}/texmf-dist \\",
        "\t%{buildroot}%{_datadir}/tlpkg %{buildroot}%{_datadir}/texlive 2>/dev/null || true",
        "rm -rf %{buildroot}%{_prefix}/texmf %{buildroot}%{_prefix}/texmf-dist 2>/dev/null || true",
        "",
        "# Empty main package — all files live in texlive-binaries.",
        "%files",
        "",
        "%files -n texlive-binaries",
        "%{_bindir}/",
        "%{_libdir}/libkpathsea.so.*",
        "%{_libdir}/libptexenc.so.*",
        "%{_libdir}/libsynctex.so.*",
        "%{_libdir}/libtexlua*.so.*",
        "%{_mandir}/man1/*",
        "",
    ]
    return "\n".join(lines)


def write_texlive_bin_package(
    packages: dict[str, TLPackage],
    analyses: dict[str, BinAnalysis],
    out_dir: Path,
    quirks: dict[str, Any],
) -> Path:
    """Write texlive-bin/ monorepo source spec + placeholder .abf.yml."""
    pkg_dir = out_dir / "texlive-bin"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    version = default_source_version(quirks)
    (pkg_dir / "texlive-bin.spec").write_text(
        generate_texlive_bin_spec(packages, analyses, quirks), encoding="utf-8"
    )
    fn = source_tarball_filename(version)
    (pkg_dir / ".abf.yml").write_text(
        f"sources:\n  {fn}: 0000000000000000000000000000000000000000\n",
        encoding="utf-8",
    )
    return pkg_dir
