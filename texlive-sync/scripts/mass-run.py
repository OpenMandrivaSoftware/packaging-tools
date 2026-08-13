#!/usr/bin/env python3
"""Apply all packages (parallel), then fire ABF builds for all packable."""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Tooling root = this file's parent/.. (texlive-sync/), unless overridden.
# Work trees and logs default under $HOME so they survive reboot (/tmp is tmpfs).
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_ROOT = _SCRIPT_DIR.parent
ROOT = Path(os.environ.get("TEXLIVE_SYNC_ROOT", str(_DEFAULT_ROOT)))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from texlive_sync.apply import already_applied, apply_package, abf_build_project
from texlive_sync.generate import generate_spec
from texlive_sync.quirks import load_quirks
from texlive_sync.tlpdb import load_tlpdb, iter_packagable

def _spec(pkg, quirks, packages, cache):
    """Generate spec with wrapper .bin folding (Provides texlive(name.bin))."""
    return generate_spec(
        pkg, quirks, packages=packages, bin_cache=cache / "_bin_cache"
    )

_home = Path.home()
LOGDIR = Path(os.environ.get("TEXLIVE_SYNC_LOGDIR", str(_home / "texlive-sync-logs")))
WORK = Path(os.environ.get("TEXLIVE_ABF_WORK", str(_home / "texlive-abf-work")))
STATE = LOGDIR / "mass-state.json"
APPLY_LOG = LOGDIR / "apply.log"
BUILD_LOG = LOGDIR / "build.log"
FAIL_LOG = LOGDIR / "failures.log"
APPLY_JOBS = int(os.environ.get("TEXLIVE_APPLY_JOBS", "8"))
BUILD_JOBS = int(os.environ.get("TEXLIVE_BUILD_JOBS", "1"))

_lock = threading.Lock()


def log(path: Path, msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text())
    return {"applied": [], "built": [], "apply_failed": {}, "build_failed": {}}


def save_state(st: dict) -> None:
    with _lock:
        STATE.write_text(json.dumps(st, indent=2, sort_keys=True) + "\n")


def main() -> int:
    packages = load_tlpdb(ROOT / "data" / "texlive.tlpdb")
    quirks = load_quirks(ROOT / "quirks.yaml")
    blocked = set(quirks.get("block") or [])
    packable = [p for p in iter_packagable(packages) if p.name not in blocked]
    st = load_state()
    applied_set = set(st.get("applied") or [])
    built_set = set(st.get("built") or [])
    WORK.mkdir(parents=True, exist_ok=True)
    cache = WORK / "_sources"

    # Mark already-good work trees
    for pkg in packable:
        if already_applied(pkg, WORK):
            applied_set.add(pkg.rpm_name())
    st["applied"] = sorted(applied_set)
    save_state(st)
    log(APPLY_LOG, f"phase1 apply start packable={len(packable)} already_good={len(applied_set)} jobs={APPLY_JOBS}")

    todo = [p for p in packable if not already_applied(p, WORK)]
    log(APPLY_LOG, f"to_apply={len(todo)}")

    def do_apply(pkg):
        name = pkg.rpm_name()
        try:
            apply_package(
                pkg, _spec(pkg, quirks, packages, WORK), WORK, sources_cache=cache
            )
            return ("ok", name, None)
        except Exception as e:
            return ("fail", name, str(e))

    done = 0
    with ThreadPoolExecutor(max_workers=APPLY_JOBS) as ex:
        futs = {ex.submit(do_apply, p): p for p in todo}
        for fut in as_completed(futs):
            status, name, err = fut.result()
            done += 1
            if status == "ok":
                applied_set.add(name)
                # Re-apply (new revision / generator shape) must rebuild.
                built_set.discard(name)
                st.get("apply_failed", {}).pop(name, None)
                log(APPLY_LOG, f"applied {name} ({done}/{len(todo)})")
            else:
                st.setdefault("apply_failed", {})[name] = err
                log(APPLY_LOG, f"FAILED apply {name}: {err}")
                log(FAIL_LOG, f"apply {name}: {err}")
            if done % 25 == 0:
                st["applied"] = sorted(applied_set)
                save_state(st)

    st["applied"] = sorted(applied_set)
    save_state(st)
    log(APPLY_LOG, f"phase1 done applied={len(applied_set)} failed={len(st.get('apply_failed') or {})}")

    # Phase 2: builds for everything applied but not yet built
    to_build = [p for p in packable if p.rpm_name() in applied_set and p.rpm_name() not in built_set]
    # Also build if applied via work tree even if not in set
    for p in packable:
        if already_applied(p, WORK) and p.rpm_name() not in built_set:
            if p not in to_build:
                to_build.append(p)
    # unique by name
    seen = set()
    uniq = []
    for p in to_build:
        if p.rpm_name() not in seen:
            seen.add(p.rpm_name())
            uniq.append(p)
    to_build = uniq
    log(BUILD_LOG, f"phase2 build start count={len(to_build)} jobs={BUILD_JOBS}")

    def do_build(pkg):
        name = pkg.rpm_name()
        try:
            out = abf_build_project(name)
            time.sleep(3)
            return ("ok", name, out)
        except Exception as e:
            time.sleep(5)
            return ("fail", name, str(e))

    done = 0
    with ThreadPoolExecutor(max_workers=BUILD_JOBS) as ex:
        futs = {ex.submit(do_build, p): p for p in to_build}
        for fut in as_completed(futs):
            status, name, info = fut.result()
            done += 1
            if status == "ok":
                built_set.add(name)
                st.get("build_failed", {}).pop(name, None)
                log(BUILD_LOG, f"build {name} ({done}/{len(to_build)})")
            else:
                st.setdefault("build_failed", {})[name] = info
                log(BUILD_LOG, f"FAILED build {name}: {info}")
                log(FAIL_LOG, f"build {name}: {info}")
                time.sleep(0.5)
            if done % 15 == 0:
                st["built"] = sorted(built_set)
                save_state(st)

    st["built"] = sorted(built_set)
    save_state(st)
    log(BUILD_LOG, f"phase2 done built={len(built_set)} failed={len(st.get('build_failed') or {})}")

    # Retry failures once
    af = dict(st.get("apply_failed") or {})
    if af:
        log(APPLY_LOG, f"retry apply {len(af)}")
        for name in list(af):
            tl = name.removeprefix("texlive-")
            pkg = packages.get(tl)
            if not pkg:
                continue
            try:
                apply_package(
                    pkg, _spec(pkg, quirks, packages, WORK), WORK, sources_cache=cache
                )
                applied_set.add(name)
                del af[name]
                log(APPLY_LOG, f"retry applied {name}")
            except Exception as e:
                af[name] = str(e)
                log(APPLY_LOG, f"retry FAILED apply {name}: {e}")
        st["apply_failed"] = af
        st["applied"] = sorted(applied_set)
        save_state(st)

    bf = dict(st.get("build_failed") or {})
    if bf:
        log(BUILD_LOG, f"retry build {len(bf)}")
        for name in list(bf):
            try:
                abf_build_project(name)
                built_set.add(name)
                del bf[name]
                log(BUILD_LOG, f"retry build {name}")
            except Exception as e:
                bf[name] = str(e)
                log(BUILD_LOG, f"retry FAILED build {name}: {e}")
            time.sleep(1)
        st["build_failed"] = bf
        st["built"] = sorted(built_set)
        save_state(st)

    # Final: build any applied that still lack built marker
    for pkg in packable:
        name = pkg.rpm_name()
        if name in built_set:
            continue
        if not already_applied(pkg, WORK):
            continue
        try:
            abf_build_project(name)
            built_set.add(name)
            log(BUILD_LOG, f"final build {name}")
        except Exception as e:
            st.setdefault("build_failed", {})[name] = str(e)
            log(BUILD_LOG, f"final FAILED build {name}: {e}")
        time.sleep(0.3)
    st["built"] = sorted(built_set)
    save_state(st)

    log(BUILD_LOG,
        f"FINISHED applied={len(applied_set)} built={len(built_set)} "
        f"apply_failed={len(st.get('apply_failed') or {})} "
        f"build_failed={len(st.get('build_failed') or {})}")
    return 0 if not st.get("apply_failed") and not st.get("build_failed") else 1


if __name__ == "__main__":
    sys.exit(main())
