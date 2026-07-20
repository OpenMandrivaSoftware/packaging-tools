"""CLI for texlive-sync."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .deps import build_layers, rpm_requires, topo_sort
from .generate import write_bin_package, write_package
from .quirks import load_quirks
from .tlpdb import (
    DEFAULT_MIRROR,
    bin_base_names,
    fetch_tlpdb,
    iter_packagable,
    load_tlpdb,
    platform_packages_for_base,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    return _project_root() / "data"


def _default_quirks() -> Path:
    return _project_root() / "quirks.yaml"


def cmd_fetch_tlpdb(args: argparse.Namespace) -> int:
    dest = Path(args.output or (_default_data_dir() / "texlive.tlpdb"))
    print(f"Fetching tlpdb from {args.mirror} ...")
    plain = fetch_tlpdb(dest, mirror=args.mirror)
    pkgs = load_tlpdb(plain)
    print(f"Wrote {plain} ({len(pkgs)} entries)")
    return 0


def _load_db(args: argparse.Namespace):
    path = Path(args.tlpdb or (_default_data_dir() / "texlive.tlpdb"))
    if not path.is_file():
        xz = Path(str(path) + ".xz")
        if xz.is_file():
            path = xz
        else:
            print(f"tlpdb not found at {path}; run fetch-tlpdb first", file=sys.stderr)
            sys.exit(1)
    return load_tlpdb(path)


def _select_names(args: argparse.Namespace, packages) -> list[str]:
    quirks = load_quirks(Path(args.quirks) if args.quirks else _default_quirks())
    blocked = set(quirks.get("block") or [])

    if getattr(args, "spike", False):
        spike_file = _project_root() / "spike-packages.txt"
        names = []
        for ln in spike_file.read_text(encoding="utf-8").splitlines():
            ln = ln.split("#", 1)[0].strip()
            if ln:
                names.append(ln)
        return names

    if args.package:
        return list(args.package)

    if args.all:
        return [
            p.name
            for p in iter_packagable(packages)
            if p.name not in blocked
        ]

    print("Specify --all, --spike, or --package NAME", file=sys.stderr)
    sys.exit(2)


def cmd_generate(args: argparse.Namespace) -> int:
    packages = _load_db(args)
    quirks = load_quirks(Path(args.quirks) if args.quirks else _default_quirks())
    blocked = set(quirks.get("block") or [])
    out = Path(args.out or (_project_root() / "out"))
    out.mkdir(parents=True, exist_ok=True)
    mirror = args.mirror or DEFAULT_MIRROR

    if getattr(args, "bin_source", False):
        return _cmd_generate_bin_source(args, packages, quirks, out, mirror)
    if getattr(args, "bin", False):
        return _cmd_generate_bin(args, packages, quirks, out, mirror, blocked)

    from .binaries import analyze_bin_base
    from .tlpdb import platform_packages_for_base

    names = _select_names(args, packages)
    cache = out / "_bin_cache"
    ok = 0
    missing = []
    folded = 0
    for name in names:
        if name in blocked:
            print(f"skip (blocked): {name}")
            continue
        pkg = packages.get(name)
        if pkg is None:
            missing.append(name)
            print(f"missing in tlpdb: {name}", file=sys.stderr)
            continue
        analysis = None
        # If this module has a platform companion that is wrapper-only, fold
        # bindir symlinks + texlive(name.bin) into the noarch package.
        if platform_packages_for_base(packages, name):
            analysis = analyze_bin_base(name, packages, cache, mirror=mirror)
            if analysis.kind != "wrapper":
                analysis = None  # natives satisfied by texlive-binaries
        pkg_dir = write_package(
            pkg, out, quirks, mirror=mirror, bin_analysis=analysis
        )
        ver_rel = f"{pkg.catalogue_version or '—'} / r{pkg.revision}"
        reqs = rpm_requires(pkg, quirks)
        extra = ""
        if analysis is not None:
            extra = f"  folded_bin={len(analysis.links)}"
            folded += 1
        print(
            f"generated {pkg_dir.name}  catalogue={ver_rel}  "
            f"requires={len(reqs)}{extra}"
        )
        ok += 1

    # manifest slice
    manifest = {
        "mirror": mirror,
        "count": ok,
        "packages": {},
    }
    for name in names:
        pkg = packages.get(name)
        if not pkg:
            continue
        manifest["packages"][name] = {
            "revision": pkg.revision,
            "catalogue_version": pkg.catalogue_version,
            "depends": pkg.depends,
            "rpm_requires": rpm_requires(pkg, quirks),
            "has_doc": pkg.has_doc_container,
            "has_src": pkg.has_src_container,
            "category": pkg.category,
        }
    man_path = out / "manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {man_path}")
    if missing:
        print(f"{len(missing)} names missing from tlpdb", file=sys.stderr)
        return 1
    return 0


def _cmd_generate_bin_source(args, packages, quirks, out, mirror) -> int:
    """Generate monorepo texlive-bin SRPM (builds natives from TL sources)."""
    from .binaries import analyze_bin_base
    from .generate import write_texlive_bin_package

    cache = out / "_bin_cache"
    bases = bin_base_names(packages)
    analyses = {}
    for base in bases:
        analyses[base] = analyze_bin_base(base, packages, cache, mirror=mirror)
    pkg_dir = write_texlive_bin_package(packages, analyses, out, quirks)
    natives = sum(1 for a in analyses.values() if a.kind == "native")
    wrappers = sum(1 for a in analyses.values() if a.kind == "wrapper")
    print(
        f"generated {pkg_dir.name}  natives={natives} wrappers={wrappers} "
        f"(wrappers fold into modules; natives -> texlive-binaries Provides)"
    )
    print(f"  spec: {pkg_dir / 'texlive-bin.spec'}")
    return 0


def _cmd_generate_bin(args, packages, quirks, out, mirror, blocked) -> int:
    """Legacy: separate wrapper-only texlive-*.bin (prefer folded modules)."""
    print(
        "note: wrapper .bin companions are folded into noarch modules; "
        "use `generate --bin-source` for the monorepo source build of natives",
        file=sys.stderr,
    )
    from .binaries import analyze_bin_base

    if args.package:
        bases = list(args.package)
    elif args.all or getattr(args, "spike", False):
        # --spike with --bin: mix of wrappers + natives (natives should skip)
        if getattr(args, "spike", False) and not args.all:
            bases = ["jadetex", "epstopdf", "pdftex", "kpathsea", "bibtex"]
        else:
            bases = bin_base_names(packages)
    else:
        print("Specify --all, --spike, or --package BASE with --bin", file=sys.stderr)
        return 2

    cache = out / "_bin_cache"
    ok = 0
    skipped_native = 0
    missing = []
    failed = []
    for base in bases:
        if base in blocked or f"{base}.bin" in blocked:
            print(f"skip (blocked): {base}.bin")
            continue
        plats = platform_packages_for_base(packages, base)
        if not plats:
            missing.append(base)
            print(f"missing platform packages for: {base}", file=sys.stderr)
            continue
        analysis = analyze_bin_base(base, packages, cache, mirror=mirror)
        if analysis.kind != "wrapper":
            print(
                f"skip (not wrapper-only, need source build): {base}.bin  "
                f"kind={analysis.kind}"
                + (f" err={analysis.error}" if analysis.error else "")
            )
            skipped_native += 1
            continue
        try:
            pkg_dir = write_bin_package(
                base,
                packages,
                out,
                quirks,
                mirror=mirror,
                cache=cache,
                analysis=analysis,
            )
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {base}.bin: {e}", file=sys.stderr)
            failed.append(base)
            continue
        names = [e.name for e in analysis.links]
        print(
            f"generated {pkg_dir.name}  r{analysis.revision}  "
            f"noarch wrappers={names}"
        )
        ok += 1
    print(
        f"bin packages generated: {ok}  skipped_native={skipped_native}  "
        f"missing={len(missing)} failed={len(failed)}"
    )
    if missing or failed:
        return 1
    return 0


def cmd_build_order(args: argparse.Namespace) -> int:
    packages = _load_db(args)
    quirks = load_quirks(Path(args.quirks) if args.quirks else _default_quirks())
    if args.package:
        only = set(args.package)
        # include transitive deps that exist in tlpdb
        expand = set(only)
        changed = True
        while changed:
            changed = False
            for n in list(expand):
                pkg = packages.get(n)
                if not pkg:
                    continue
                for d in pkg.depends:
                    if d.endswith(".ARCH"):
                        d = d[: -len(".ARCH")]
                    if d in packages and d not in expand:
                        expand.add(d)
                        changed = True
        only = expand
    else:
        only = None
    layers = build_layers(packages, quirks, only)
    for i, layer in enumerate(layers):
        print(f"# layer {i} ({len(layer)} packages)")
        for n in layer:
            print(n)
    order_path = Path(args.out or (_project_root() / "out" / "build-order.txt"))
    order_path.parent.mkdir(parents=True, exist_ok=True)
    flat = topo_sort(packages, quirks, only)
    order_path.write_text("\n".join(flat) + "\n", encoding="utf-8")
    print(f"# wrote {order_path} ({len(flat)} packages, {len(layers)} layers)", file=sys.stderr)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from .apply import apply_bin_package, apply_many, abf_build_project
    from .generate import generate_bin_spec, generate_spec
    from .tlpdb import platform_packages_for_base

    packages = _load_db(args)
    quirks = load_quirks(Path(args.quirks) if args.quirks else _default_quirks())
    mirror = args.mirror or DEFAULT_MIRROR
    work = Path(args.work or (_project_root() / "work" / "abf"))

    if getattr(args, "bin", False):
        return _cmd_apply_bin(args, packages, quirks, mirror, work)

    names = _select_names(args, packages)
    bin_cache = work / "_bin_cache"
    items: list = []
    for name in names:
        pkg = packages.get(name)
        if pkg is None:
            print(f"missing in tlpdb: {name}", file=sys.stderr)
            continue
        items.append(
            (
                pkg,
                generate_spec(
                    pkg,
                    quirks,
                    mirror=mirror,
                    packages=packages,
                    bin_cache=bin_cache,
                ),
            )
        )

    def progress(msg: str) -> None:
        print(msg, flush=True)

    results = apply_many(
        items,
        work,
        mirror=mirror,
        dry_run=bool(args.dry_run),
        build=bool(args.build),
        limit=args.limit,
        jobs=int(args.jobs or 1),
        on_progress=progress,
    )
    print(
        f"done: ok={len(results['ok'])} skipped={len(results.get('skipped') or [])} "
        f"failed={len(results['failed'])} built={len(results['built'])} "
        f"build_failed={len(results['build_failed'])}"
    )
    if results["failed"]:
        for f in results["failed"][:20]:
            print(f"  FAIL {f['name']}: {f['error']}", file=sys.stderr)
        return 1
    return 0


def _cmd_apply_bin(args, packages, quirks, mirror, work) -> int:
    """Apply noarch wrapper .bin packages; skip bases with native ELFs."""
    from .apply import abf_build_project, apply_bin_package, already_applied_bin
    from .binaries import analyze_bin_base
    from .generate import generate_bin_spec

    blocked = set(quirks.get("block") or [])
    if args.package:
        bases = list(args.package)
    elif args.all:
        bases = bin_base_names(packages)
    elif getattr(args, "spike", False):
        bases = ["jadetex", "epstopdf", "pdftex", "kpathsea", "bibtex"]
    else:
        print("Specify --all, --spike, or --package BASE with --bin", file=sys.stderr)
        return 2
    if args.limit:
        bases = bases[: args.limit]

    cache = work / "_bin_cache"
    ok = fail = built = skipped = 0
    for base in bases:
        if base in blocked or f"{base}.bin" in blocked:
            print(f"skip blocked {base}.bin")
            continue
        plats = platform_packages_for_base(packages, base)
        if not plats:
            print(f"FAIL {base}.bin: no platform packages", file=sys.stderr)
            fail += 1
            continue
        analysis = analyze_bin_base(base, packages, cache, mirror=mirror)
        if analysis.kind != "wrapper":
            print(
                f"skip (native/needs source build): {base}.bin kind={analysis.kind}"
            )
            skipped += 1
            continue
        if already_applied_bin(base, analysis.revision, work) and not args.force:
            print(f"skip already applied texlive-{base}.bin r{analysis.revision}")
            skipped += 1
            continue
        try:
            spec = generate_bin_spec(base, analysis, packages, quirks)
            apply_bin_package(
                base,
                packages,
                spec,
                work,
                mirror=mirror,
                dry_run=bool(args.dry_run),
                revision=analysis.revision,
            )
            print(f"applied texlive-{base}.bin (noarch wrappers)")
            ok += 1
            if args.build and not args.dry_run:
                # noarch: one ABF task is enough; still list primary arches
                # so the noarch package lands in all arch repos.
                abf_build_project(
                    f"texlive-{base}.bin",
                    arches=("znver1", "x86_64", "aarch64"),
                )
                print(f"build started texlive-{base}.bin (noarch)")
                built += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL texlive-{base}.bin: {e}", file=sys.stderr)
            fail += 1
    print(f"bin done: ok={ok} failed={fail} skipped={skipped} built={built}")
    return 1 if fail else 0



def cmd_build(args: argparse.Namespace) -> int:
    from .apply import abf_build_project

    packages = _load_db(args)
    names = _select_names(args, packages)
    if args.limit:
        names = names[: args.limit]
    failed = 0
    for name in names:
        rpm = f"texlive-{name}"
        try:
            out = abf_build_project(rpm)
            print(f"build started {rpm}")
            if args.verbose:
                print(out)
        except Exception as e:  # noqa: BLE001
            print(f"build failed {rpm}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="texlive-sync",
        description=f"OpenMandriva TeX Live packaging automation v{__version__}",
    )
    parser.add_argument("--tlpdb", help="Path to texlive.tlpdb (or .xz)")
    parser.add_argument("--quirks", help="Path to quirks.yaml")
    parser.add_argument(
        "--mirror",
        default=DEFAULT_MIRROR,
        help=f"tlnet mirror (default: {DEFAULT_MIRROR})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch-tlpdb", help="Download texlive.tlpdb")
    p_fetch.add_argument("-o", "--output", help="Output path for plain tlpdb")
    p_fetch.set_defaults(func=cmd_fetch_tlpdb)

    p_gen = sub.add_parser("generate", help="Generate specs")
    p_gen.add_argument("--all", action="store_true", help="All packagable TL packages")
    p_gen.add_argument("--spike", action="store_true", help="Phase-0 sample set")
    p_gen.add_argument(
        "--bin",
        action="store_true",
        help="(legacy) separate wrapper-only texlive-*.bin packages",
    )
    p_gen.add_argument(
        "--bin-source",
        action="store_true",
        dest="bin_source",
        help="Generate monorepo texlive-bin SRPM (build natives from source)",
    )
    p_gen.add_argument(
        "-p",
        "--package",
        action="append",
        dest="package",
        help="TL package name (or bin base with --bin; repeatable)",
    )
    p_gen.add_argument("--out", help="Output directory (default: ./out)")
    p_gen.set_defaults(func=cmd_generate)

    p_ord = sub.add_parser("build-order", help="Print dependency build order")
    p_ord.add_argument(
        "-p",
        "--package",
        action="append",
        dest="package",
        help="Limit to package + deps (repeatable)",
    )
    p_ord.add_argument("--out", help="Write flat order to this file")
    p_ord.set_defaults(func=cmd_build_order)

    p_apply = sub.add_parser("apply", help="Store sources, update git, push to ABF/GitHub")
    p_apply.add_argument("--all", action="store_true", help="All packagable TL packages")
    p_apply.add_argument("--spike", action="store_true", help="Phase-0 sample set")
    p_apply.add_argument(
        "--bin",
        action="store_true",
        help="Apply texlive-*.bin arch companion packages",
    )
    p_apply.add_argument(
        "-p",
        "--package",
        action="append",
        dest="package",
        help="TL package name (or bin base with --bin; repeatable)",
    )
    p_apply.add_argument("--work", help="Working directory for git checkouts")
    p_apply.add_argument("--dry-run", action="store_true", help="No store/commit/push")
    p_apply.add_argument("--build", action="store_true", help="Also start ABF builds")
    p_apply.add_argument(
        "--force",
        action="store_true",
        help="Re-apply even if work tree already matches generator shape",
    )
    p_apply.add_argument("--limit", type=int, help="Max packages to process")
    p_apply.add_argument("--jobs", type=int, default=6, help="Parallel apply workers (default 6)")
    p_apply.set_defaults(func=cmd_apply)

    p_build = sub.add_parser("build", help="Start ABF builds for packages")
    p_build.add_argument("--all", action="store_true")
    p_build.add_argument("--spike", action="store_true")
    p_build.add_argument(
        "-p",
        "--package",
        action="append",
        dest="package",
        help="TL package name (repeatable)",
    )
    p_build.add_argument("--limit", type=int)
    p_build.add_argument("-v", "--verbose", action="store_true")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
