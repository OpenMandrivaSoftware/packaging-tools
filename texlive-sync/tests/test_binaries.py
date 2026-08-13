"""Tests for wrapper-only .bin classification and install targets."""

from __future__ import annotations

import io
import tarfile

from texlive_sync.binaries import (
    BinEntry,
    classify_entries,
    inspect_platform_tar,
    wrapper_bindir_target,
    wrapper_requires,
)
from texlive_sync.generate import generate_bin_spec
from texlive_sync.binaries import BinAnalysis
from texlive_sync.tlpdb import TLPackage


def _tar_with(members: list[tuple[str, str | None, bytes | None]]) -> bytes:
    """Build an xz tar: (path, link_target|None, file_bytes|None)."""
    buf = io.BytesIO()
    # tarfile xz needs a real write; use plain tar then we can use r: mode...
    # inspect_platform_tar expects r:xz — build xz.
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for path, link, data in members:
            info = tarfile.TarInfo(name=path)
            if link is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link
                info.size = 0
                tf.addfile(info)
            else:
                payload = data or b""
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
    # recompress as xz
    import lzma
    return lzma.compress(raw.getvalue())


def test_inspect_wrapper_links():
    data = _tar_with(
        [
            ("bin/x86_64-linux/jadetex", "pdftex", None),
            ("bin/x86_64-linux/pdfjadetex", "pdftex", None),
            ("tlpkg/tlpobj/ignored", None, b"x"),
        ]
    )
    ents = inspect_platform_tar(data)
    assert [e.name for e in ents] == ["jadetex", "pdfjadetex"]
    assert all(e.kind == "link" for e in ents)
    assert classify_entries(ents) == "wrapper"


def test_inspect_native_elf():
    data = _tar_with(
        [
            ("bin/x86_64-linux/pdftex", None, b"\x7fELF\x02\x01"),
            ("bin/x86_64-linux/etex", "pdftex", None),
        ]
    )
    ents = inspect_platform_tar(data)
    assert classify_entries(ents) == "native"


def test_wrapper_bindir_target_engine_and_script():
    assert wrapper_bindir_target("pdftex") == "pdftex"
    assert (
        wrapper_bindir_target("../../texmf-dist/scripts/epstopdf/epstopdf.pl")
        == "%{_texmfdistdir}/scripts/epstopdf/epstopdf.pl"
    )


def test_wrapper_requires():
    ents = [
        BinEntry("jadetex", "link", "pdftex"),
        BinEntry("epstopdf", "link", "../../texmf-dist/scripts/epstopdf/epstopdf.pl"),
        BinEntry("repstopdf", "link", "epstopdf"),
    ]
    reqs = wrapper_requires("epstopdf", ents)
    assert "texlive(epstopdf)" in reqs
    assert "/usr/bin/pdftex" in reqs
    # sibling alias epstopdf is provided by this package — not an external req
    assert "/usr/bin/epstopdf" not in reqs


def test_generate_bin_spec_no_sources_noarch():
    analysis = BinAnalysis(
        base="jadetex",
        kind="wrapper",
        revision=3185,
        entries=[
            BinEntry("jadetex", "link", "pdftex"),
            BinEntry("pdfjadetex", "link", "pdftex"),
        ],
    )
    packages = {
        "jadetex": TLPackage(name="jadetex", revision=79618, catalogue_license="LPPL"),
    }
    spec = generate_bin_spec("jadetex", analysis, packages, {})
    assert "BuildArch:\tnoarch" in spec
    assert "Source" not in spec or "Source0" not in spec
    assert "x86_64-linux.tar.xz" not in spec
    assert "ln -sfn pdftex %{buildroot}%{_bindir}/jadetex" in spec
    assert "ln -sfn pdftex %{buildroot}%{_bindir}/pdfjadetex" in spec
    assert "Provides:\ttexlive(%{tl_name}.bin)" in spec
    assert "Requires:\t/usr/bin/pdftex" in spec


def test_generate_rejects_native():
    analysis = BinAnalysis(
        base="pdftex",
        kind="native",
        revision=1,
        entries=[BinEntry("pdftex", "elf", None)],
    )
    try:
        generate_bin_spec("pdftex", analysis, {}, {})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "source" in str(e).lower() or "native" in str(e).lower()


def test_tl_bin_links_global():
    from texlive_sync.binaries import tl_bin_links_global
    s = tl_bin_links_global(
        [
            ("jadetex", "pdftex"),
            ("epstopdf", "%{_texmfdistdir}/scripts/epstopdf/epstopdf.pl"),
        ]
    )
    assert s == "jadetex:pdftex epstopdf:%{_texmfdistdir}/scripts/epstopdf/epstopdf.pl"


def test_wrapper_link_pairs_skips_man_doc_tree():
    """Upstream texlive-scripts has man -> texmf-dist/doc/man; never fold it."""
    from texlive_sync.binaries import all_bin_link_pairs, wrapper_link_pairs

    ents = [
        BinEntry(
            "fmtutil",
            "link",
            "../../texmf-dist/scripts/texlive/fmtutil.pl",
        ),
        BinEntry("man", "link", "../../texmf-dist/doc/man"),
        BinEntry("mktexfmt", "link", "fmtutil"),
        BinEntry("texhash", "link", "mktexlsr"),
    ]
    pairs = wrapper_link_pairs(ents)
    names = [n for n, _ in pairs]
    assert "man" not in names
    assert "fmtutil" in names
    assert "mktexfmt" in names
    assert "texhash" in names
    assert all("/doc/man" not in t for _, t in pairs)
    # Extras add the missing mktexlsr command (texhash target).
    all_pairs = all_bin_link_pairs(ents, tl_name="texlive-scripts")
    all_names = [n for n, _ in all_pairs]
    assert "mktexlsr" in all_names
    assert "man" not in all_names


def test_generate_spec_does_not_fold_man_link():
    from texlive_sync.generate import generate_spec
    from texlive_sync.binaries import BinAnalysis, BinEntry
    from texlive_sync.tlpdb import TLPackage

    pkg = TLPackage(
        name="texlive-scripts",
        revision=79950,
        depends=["texlive-scripts.ARCH", "texlive.infra"],
        catalogue_license="LPPL",
        shortdesc="TeX Live infrastructure programs",
    )
    analysis = BinAnalysis(
        base="texlive-scripts",
        kind="wrapper",
        revision=64356,
        entries=[
            BinEntry(
                "fmtutil",
                "link",
                "../../texmf-dist/scripts/texlive/fmtutil.pl",
            ),
            BinEntry("man", "link", "../../texmf-dist/doc/man"),
            BinEntry("texhash", "link", "mktexlsr"),
        ],
    )
    spec = generate_spec(pkg, {}, bin_analysis=analysis)
    assert "man:" not in spec
    assert "/doc/man" not in spec
    assert "fmtutil:%{_texmfdistdir}/scripts/texlive/fmtutil.pl" in spec
    assert "texhash:mktexlsr" in spec


def test_generate_spec_folds_wrappers():
    from texlive_sync.generate import generate_spec
    from texlive_sync.binaries import BinAnalysis, BinEntry
    from texlive_sync.tlpdb import TLPackage

    pkg = TLPackage(
        name="jadetex",
        revision=100,
        depends=["jadetex.ARCH", "pdftex"],
        catalogue_license="LPPL",
        shortdesc="JadeTeX",
    )
    analysis = BinAnalysis(
        base="jadetex",
        kind="wrapper",
        revision=50,
        entries=[
            BinEntry("jadetex", "link", "pdftex"),
            BinEntry("pdfjadetex", "link", "pdftex"),
        ],
    )
    spec = generate_spec(pkg, {}, bin_analysis=analysis)
    assert "%global tl_bin_links jadetex:pdftex pdfjadetex:pdftex" in spec
    assert "Provides:\ttexlive(%{tl_name}.bin)" in spec
    # jadetex.ARCH → Requires texlive(jadetex.bin); same package Provides it
    # (RPM self-satisfies). Natives without fold still need the external
    # texlive-binaries package for that capability.
    assert "Requires:\ttexlive(jadetex.bin)" in spec
    assert "Requires:\ttexlive(pdftex)" in spec


def test_generate_texlive_bin_has_source_and_provides():
    from texlive_sync.generate import generate_texlive_bin_spec
    from texlive_sync.binaries import BinAnalysis, BinEntry

    analyses = {
        "pdftex": BinAnalysis(
            "pdftex",
            "native",
            [BinEntry("pdftex", "elf", None)],
            revision=78082,
        ),
        "kpathsea": BinAnalysis(
            "kpathsea",
            "native",
            [BinEntry("kpsewhich", "elf", None)],
            revision=77900,
        ),
        "jadetex": BinAnalysis(
            "jadetex",
            "wrapper",
            [BinEntry("jadetex", "link", "pdftex")],
            revision=1,
        ),
        "biber": BinAnalysis(
            "biber",
            "native",
            [BinEntry("biber", "elf", None)],
            revision=1,
        ),
    }
    packages = {}
    quirks = {"texlive_source": {"version": "20260301"}}
    spec = generate_texlive_bin_spec(packages, analyses, quirks)
    assert "Source0:" in spec
    assert "texlive-20260301-source.tar.xz" in spec
    assert "Provides:\ttexlive(pdftex.bin)" in spec
    assert "Provides:\ttexlive(kpathsea.bin)" in spec
    assert "texlive(jadetex.bin)" not in spec  # wrapper
    assert "texlive(biber.bin)" not in spec  # external
    assert "%package -n texlive-binaries" in spec
    assert "../configure" in spec
