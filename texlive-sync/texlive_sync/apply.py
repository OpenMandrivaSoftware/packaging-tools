"""Apply generated packages to GitHub/ABF and trigger builds."""

from __future__ import annotations

import fcntl

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .tlpdb import (
    DEFAULT_MIRROR,
    TLPackage,
    archive_filename,
    archive_url,
    platform_packages_for_base,
)


GIT_ORG = "OpenMandrivaAssociation"
ABF_GROUP = "openmandriva"
DEFAULT_ARCHES = ("znver1", "x86_64", "aarch64")
DEFAULT_SAVE = "cooker/main"

# mirrors.ctan.org geo-redirects; some backends lag on brand-new revisions.
# Fall back to known full mirrors on 404/403.
_FALLBACK_MIRRORS = (
    "https://mirrors.rit.edu/CTAN/systems/texlive/tlnet",
    "https://ctan.math.illinois.edu/systems/texlive/tlnet",
    "https://mirror.math.princeton.edu/pub/CTAN/systems/texlive/tlnet",
    "https://ftp.tu-chemnitz.de/pub/tug/ctan/systems/texlive/tlnet",
    "https://mirror.ctan.org/systems/texlive/tlnet",
)


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Avoid interactive ssh prompts hanging automation
    full_env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        capture_output=capture,
        env=full_env,
    )



_ABF_LOCK_PATH = Path.home() / ".abf_cli.lock"

def _abf_locked(cmd: list[str], **kwargs):
    """Serialize all abf CLI calls — concurrent abf corrupts ~/.abf_projects."""
    _ABF_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _ABF_LOCK_PATH.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            return _run(cmd, **kwargs)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_url(url: str, path: Path, *, timeout: int = 120) -> None:
    """Download *url* to *path* (atomic rename from .part)."""
    tmp = path.with_suffix(path.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "texlive-sync/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        if tmp.stat().st_size <= 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"empty download: {url}")
        tmp.rename(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _mirror_candidates(primary: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in (primary, DEFAULT_MIRROR, *_FALLBACK_MIRRORS):
        m = m.rstrip("/")
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def download_url_with_fallback(
    relative_path: str,
    dest: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    timeout: int = 120,
    attempts_per_mirror: int = 2,
) -> Path:
    """
    Download ``archive/...`` (or other path under tlnet) trying primary then
    fallback mirrors. CTAN geo-DNS often returns a lagging backend for new
    revisions; retrying other mirrors clears most 404s.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    rel = relative_path.lstrip("/")
    errors: list[str] = []
    for base in _mirror_candidates(mirror):
        url = f"{base}/{rel}"
        for attempt in range(1, attempts_per_mirror + 1):
            try:
                _download_url(url, dest, timeout=timeout)
                return dest
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url} try{attempt}: {e}")
                time.sleep(min(3, attempt))
    raise RuntimeError(
        f"download failed for {rel} after trying {len(_mirror_candidates(mirror))} "
        f"mirrors: {errors[-1] if errors else 'unknown'}"
    )


def download_sources(
    pkg: TLPackage,
    dest: Path,
    mirror: str = DEFAULT_MIRROR,
) -> list[Path]:
    """Download run/doc/source archives into dest. Returns list of files."""
    dest.mkdir(parents=True, exist_ok=True)
    kinds: list[str] = ["run"]
    if pkg.has_doc_container:
        kinds.append("doc")
    if pkg.has_src_container:
        kinds.append("source")
    out: list[Path] = []
    for kind in kinds:
        fn = archive_filename(pkg.name, pkg.revision, kind)
        path = dest / fn
        if path.is_file() and path.stat().st_size > 0:
            out.append(path)
            continue
        # archive/<name>.r<rev>.tar.xz relative to tlnet root
        url = archive_url(mirror, pkg.name, pkg.revision, kind)
        # strip to path after /tlnet/ when possible; else use archive/fn
        rel = f"archive/{fn}"
        if "/archive/" in url:
            rel = "archive/" + url.rsplit("/archive/", 1)[-1]
        download_url_with_fallback(rel, path, mirror=mirror)
        out.append(path)
    return out


def write_abf_yml(paths: list[Path], dest: Path) -> None:
    lines = ["sources:"]
    for p in sorted(paths, key=lambda x: x.name):
        lines.append(f"  {p.name}: {sha1_file(p)}")
    lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")



def already_applied(pkg: TLPackage, work_root: Path) -> bool:
    """True if work checkout matches current generator shape + revision."""
    proj = work_root / pkg.rpm_name()
    spec = proj / f"{pkg.rpm_name()}.spec"
    if not spec.is_file() or not (proj / ".git").is_dir():
        return False
    try:
        body = spec.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if f"%global tl_revision {pkg.revision}" not in body:
        return False
    if "BuildSystem:" not in body:
        return False
    if "%texlive_base_requires" in body:
        return False
    if "Requires(pre)" in body:
        return False
    # texlive.infra is system-mapped to texlive-tlpkg; old specs that still
    # require the virtual capability are stale.
    if "texlive(texlive.infra)" in body:
        return False
    # TL install-tree convenience man -> doc/man must never be a bindir link.
    if "man:%{_texmfdistdir}/doc/man" in body or "man:%{_texmfdir}/doc/man" in body:
        return False
    # texlive-scripts must ship /usr/bin/mktexlsr (texhash target; script is
    # in this package, bindir link used to live in blocked texlive.infra).
    if pkg.name == "texlive-scripts" and "mktexlsr:" not in body:
        return False
    # mktexpk defaults to gsftopk; extra_requires must be in the spec.
    if pkg.name == "texlive-scripts" and "texlive(gsftopk)" not in body:
        return False
    # Hyphen packs must install language.dat.d drop-ins for filetriggers.
    from .hyphen import has_hyphen_executes

    if has_hyphen_executes(pkg) and "%{_texmf_language_dat_d}" not in body:
        return False
    from .maps import has_map_executes

    if has_map_executes(pkg) and "%{_texmf_updmap_d}" not in body:
        return False
    # Wrapper modules (depend name.ARCH, pure bindir symlinks) must fold
    # texlive(name.bin) Provides + tl_bin_links. Natives correctly leave the
    # Provide to monorepo texlive-binaries, so only treat missing Provide as
    # stale when tl_bin_links is also absent *and* the package is known to be
    # a folded wrapper candidate via the scripts-in-runfiles + .ARCH pattern
    # is too weak alone — mass re-fold uses an explicit wrapper list / force.
    return True


def already_applied_bin(base: str, revision: int, work_root: Path) -> bool:
    """True if texlive-<base>.bin work tree matches current wrapper generator shape."""
    rpm = f"texlive-{base}.bin"
    proj = work_root / rpm
    spec = proj / f"{rpm}.spec"
    if not spec.is_file() or not (proj / ".git").is_dir():
        return False
    try:
        body = spec.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if f"%global tl_revision {revision}" not in body:
        return False
    if "BuildArch:" not in body or "noarch" not in body:
        return False
    # Must not ship platform prebuilt archives as sources
    if "x86_64-linux.tar.xz" in body or "aarch64-linux.tar.xz" in body:
        return False
    if "texlive(%{tl_name}.bin)" not in body:
        return False
    if "ln -sfn" not in body:
        return False
    return True


def abf_store(path: Path, cwd: Path) -> str:
    """Upload to file-store; return sha1 (from abf output or local)."""
    cp = _abf_locked(["abf", "store", str(path)], cwd=cwd, check=False)
    text = (cp.stdout or "") + (cp.stderr or "")
    # Prefer local sha1 — store may already have the file
    digest = sha1_file(path)
    if cp.returncode != 0 and "already" not in text.lower() and "exist" not in text.lower():
        # Some abf versions still return 0; only fail on hard errors
        if "error" in text.lower() and "already" not in text.lower():
            raise RuntimeError(f"abf store failed for {path.name}: {text.strip()}")
    return digest


def ensure_github_repo(rpm_name: str) -> None:
    """Create OpenMandrivaAssociation/<rpm_name> on GitHub if missing.

    Retries on secondary rate limits. Raises if the repo still cannot be
    created (so apply never pretends a local-only tree is published).
    """
    # Fast path: does the remote already answer?
    url = f"git@github.com:{GIT_ORG}/{rpm_name}.git"
    probe = _run(["git", "ls-remote", url, "HEAD"], check=False)
    if probe.returncode == 0:
        return

    last = ""
    for attempt in range(1, 10):
        gcp = _run(
            [
                "gh",
                "repo",
                "create",
                f"{GIT_ORG}/{rpm_name}",
                "--public",
                "--description",
                f"TeX Live package {rpm_name}",
            ],
            check=False,
        )
        gtext = ((gcp.stdout or "") + (gcp.stderr or "")).strip()
        last = gtext
        glow = gtext.lower()
        if gcp.returncode == 0 or "already exists" in glow or "name already exists" in glow:
            # Confirm reachable (new empty repos still return 0 from ls-remote)
            for delay in (1, 2, 4):
                time.sleep(delay)
                probe = _run(["git", "ls-remote", url, "HEAD"], check=False)
                # empty repo: ls-remote succeeds with empty stdout
                if probe.returncode == 0:
                    return
                # Some GH timing: repo create ok but ssh not ready yet
            probe = _run(["git", "ls-remote", url, "HEAD"], check=False)
            if probe.returncode == 0:
                return
            # fall through to retry create/probe
        if "too quickly" in glow or "rate limit" in glow or "secondary rate" in glow:
            time.sleep(min(180, 20 * attempt))
            continue
        time.sleep(min(60, 5 * attempt))

    raise RuntimeError(
        f"failed to ensure GitHub repo {GIT_ORG}/{rpm_name}: {last[:400]}"
    )


def git_clone_or_pull(rpm_name: str, work_root: Path) -> Path:
    """Clone OpenMandrivaAssociation/<rpm_name> into work_root.

    Creates the GitHub repo (via gh) and ABF project (via abf create_empty)
    when missing. Never returns a local-only tree without a live origin.
    """
    dest = work_root / rpm_name
    url = f"git@github.com:{GIT_ORG}/{rpm_name}.git"

    if dest.is_dir() and (dest / ".git").is_dir():
        # Ensure origin points at GitHub and the remote exists before we touch files
        _run(["git", "remote", "remove", "origin"], cwd=dest, check=False)
        _run(["git", "remote", "add", "origin", url], cwd=dest, check=False)
        ensure_github_repo(rpm_name)
        _run(["git", "fetch", "origin"], cwd=dest, check=False)
        _run(["git", "checkout", "master"], cwd=dest, check=False)
        # empty new repos may have no master yet — ignore pull failures
        _run(["git", "pull", "--ff-only", "origin", "master"], cwd=dest, check=False)
        return dest
    if dest.exists():
        shutil.rmtree(dest)

    def try_clone() -> bool:
        if dest.exists():
            shutil.rmtree(dest)
        cp = _run(["git", "clone", url, str(dest)], check=False)
        return cp.returncode == 0 and (dest / ".git").is_dir()

    if try_clone():
        return dest

    # Ensure GitHub repository exists (ABF create_empty does not create it)
    ensure_github_repo(rpm_name)

    # Ensure ABF project exists (for builds)
    _run(
        [
            "abf",
            "create_empty",
            "--description",
            f"TeX Live package {rpm_name}",
            rpm_name,
            ABF_GROUP,
        ],
        check=False,
    )

    for delay in (1, 2, 4, 8):
        time.sleep(delay)
        if try_clone():
            return dest

    # Last resort: init local repo only after GitHub is confirmed reachable
    ensure_github_repo(rpm_name)
    dest.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "master"], cwd=dest, check=True)
    _run(["git", "remote", "remove", "origin"], cwd=dest, check=False)
    _run(["git", "remote", "add", "origin", url], cwd=dest, check=True)
    return dest


def apply_package(
    pkg: TLPackage,
    spec_text: str,
    work_root: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    sources_cache: Path | None = None,
    dry_run: bool = False,
    commit: bool = True,
    push: bool = True,
    message: str | None = None,
) -> Path:
    """
    Update (or create) the ABF/GitHub project for pkg:
      - clone/pull
      - download + abf store sources
      - write spec + .abf.yml
      - commit + push
    Returns the project directory.
    """
    rpm_name = pkg.rpm_name()
    proj = git_clone_or_pull(rpm_name, work_root)
    cache = sources_cache or (work_root / "_sources")
    files = download_sources(pkg, cache, mirror=mirror)

    # Copy sources into project for abf store (store needs a path)
    local_sources: list[Path] = []
    for f in files:
        target = proj / f.name
        if not target.is_file() or sha1_file(target) != sha1_file(f):
            shutil.copy2(f, target)
        local_sources.append(target)

    if not dry_run:
        for f in local_sources:
            abf_store(f, proj)

    write_abf_yml(local_sources, proj / ".abf.yml")
    (proj / f"{rpm_name}.spec").write_text(spec_text, encoding="utf-8")

    # Remove any leftover local tarballs from git (file-store only)
    for f in local_sources:
        _run(["git", "rm", "-f", "--cached", f.name], cwd=proj, check=False)
        # Keep file on disk optional; update.sh removed them — remove for cleanliness
        if f.is_file():
            f.unlink()

    # Drop obsolete non-revisioned or old-revision tarball references handled by yml only

    _run(["git", "add", f"{rpm_name}.spec", ".abf.yml"], cwd=proj, check=True)

    msg = message or f"Update to TeX Live r{pkg.revision} (texlive-sync)"
    if dry_run:
        return proj

    st = _run(["git", "status", "--porcelain"], cwd=proj, check=True)
    if not (st.stdout or "").strip():
        return proj  # nothing to commit

    if commit:
        ccp = _run(["git", "commit", "-m", msg], cwd=proj, check=False)
        if ccp.returncode != 0:
            # retry once (transient lock / identity races under parallel apply)
            time.sleep(1)
            _run(["git", "add", f"{rpm_name}.spec", ".abf.yml"], cwd=proj, check=False)
            _run(["git", "commit", "-m", msg], cwd=proj, check=True)
    if push and commit:
        # Make sure origin exists before push (guards local-only trees)
        ensure_github_repo(rpm_name)
        pcp = _run(["git", "push", "origin", "HEAD"], cwd=proj, check=False)
        if pcp.returncode != 0:
            time.sleep(2)
            pcp2 = _run(["git", "push", "origin", "HEAD"], cwd=proj, check=False)
            if pcp2.returncode != 0:
                err = ((pcp2.stdout or "") + (pcp2.stderr or "") or
                       (pcp.stdout or "") + (pcp.stderr or "")).strip()
                raise RuntimeError(f"git push failed for {rpm_name}: {err[:500]}")
    return proj


def apply_bin_package(
    base: str,
    packages: dict[str, TLPackage],
    spec_text: str,
    work_root: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    sources_cache: Path | None = None,
    dry_run: bool = False,
    commit: bool = True,
    push: bool = True,
    message: str | None = None,
    revision: int | None = None,
) -> Path:
    """Apply a noarch wrapper ``texlive-<base>.bin`` to GitHub/ABF.

    No platform tarballs are stored or uploaded — the spec only creates
    symlinks. ``sources_cache`` is unused (kept for call-site compatibility).
    """
    plat_pkgs = platform_packages_for_base(packages, base)
    if not plat_pkgs and revision is None:
        raise RuntimeError(f"no platform packages for {base}")
    rev = revision if revision is not None else max(p.revision for p in plat_pkgs.values())
    rpm_name = f"texlive-{base}.bin"
    proj = git_clone_or_pull(rpm_name, work_root)

    # Wrapper packages have no source archives.
    (proj / ".abf.yml").write_text("sources: {}\n", encoding="utf-8")
    (proj / f"{rpm_name}.spec").write_text(spec_text, encoding="utf-8")

    # Drop any previously committed platform tarballs / stale sources.
    for stale in proj.glob("*.tar.xz"):
        _run(["git", "rm", "-f", "--cached", stale.name], cwd=proj, check=False)
        if stale.is_file():
            stale.unlink()

    _run(["git", "add", f"{rpm_name}.spec", ".abf.yml"], cwd=proj, check=True)
    msg = message or (
        f"Update wrapper companion to TeX Live r{rev} "
        f"(noarch symlinks only; texlive-sync)"
    )
    if dry_run:
        return proj

    st = _run(["git", "status", "--porcelain"], cwd=proj, check=True)
    if not (st.stdout or "").strip():
        return proj

    if commit:
        ccp = _run(["git", "commit", "-m", msg], cwd=proj, check=False)
        if ccp.returncode != 0:
            time.sleep(1)
            _run(["git", "add", f"{rpm_name}.spec", ".abf.yml"], cwd=proj, check=False)
            _run(["git", "commit", "-m", msg], cwd=proj, check=True)
    if push and commit:
        ensure_github_repo(rpm_name)
        pcp = _run(["git", "push", "origin", "HEAD"], cwd=proj, check=False)
        if pcp.returncode != 0:
            time.sleep(2)
            pcp2 = _run(["git", "push", "origin", "HEAD"], cwd=proj, check=False)
            if pcp2.returncode != 0:
                err = ((pcp2.stdout or "") + (pcp2.stderr or "") or
                       (pcp.stdout or "") + (pcp.stderr or "")).strip()
                raise RuntimeError(f"git push failed for {rpm_name}: {err[:500]}")
    return proj


def abf_build_project(
    rpm_name: str,
    *,
    arches: tuple[str, ...] = DEFAULT_ARCHES,
    save_to: str = DEFAULT_SAVE,
    auto_publish: bool = True,
    update_type: str = "enhancement",
) -> str:
    """Kick off an ABF build. Returns combined stdout/stderr."""
    proj = f"{ABF_GROUP}/{rpm_name}"
    # New projects from create_empty are not linked to cooker/main until added.
    _abf_locked(["abf", "add", save_to, "-p", proj], check=False)
    cmd = [
        "abf",
        "build",
        "-p",
        proj,
        "-b",
        "master",
        "-s",
        save_to,
        "--update-type",
        update_type,
        "--skip-personal",
        "--no-extra-tests",
    ]
    if auto_publish:
        cmd.append("--auto-publish")
    for a in arches:
        cmd.extend(["-a", a])

    last_text = ""
    for attempt in range(1, 8):
        cp = _abf_locked(cmd, check=False, capture=True)
        text = ((cp.stdout or "") + (cp.stderr or "")).strip()
        last_text = text
        if cp.returncode == 0:
            return text
        low = text.lower()
        # empty output often means rate-limit / silent abf failure
        if (
            cp.returncode != 0
            and (
                not text
                or "rate limit" in low
                or "403" in low
                or "too many" in low
            )
        ):
            time.sleep(min(120, 15 * attempt))
            continue
        break
    raise RuntimeError(f"abf build failed for {rpm_name}: {last_text}")



def apply_many(
    items: list[tuple[TLPackage, str]],
    work_root: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    dry_run: bool = False,
    build: bool = False,
    limit: int | None = None,
    jobs: int = 1,
    skip_current: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Apply a list of (pkg, spec_text). Optionally trigger ABF builds."""
    work_root.mkdir(parents=True, exist_ok=True)
    cache = work_root / "_sources"
    results: dict[str, Any] = {
        "ok": [],
        "failed": [],
        "built": [],
        "build_failed": [],
        "skipped": [],
    }
    if limit is not None:
        items = items[:limit]

    def one(item: tuple[TLPackage, str]) -> tuple[str, str, str | None]:
        pkg, spec = item
        name = pkg.rpm_name()
        if skip_current and already_applied(pkg, work_root):
            return ("skipped", name, None)
        try:
            apply_package(
                pkg,
                spec,
                work_root,
                mirror=mirror,
                sources_cache=cache,
                dry_run=dry_run,
            )
            if build and not dry_run:
                try:
                    abf_build_project(name)
                    return ("built", name, None)
                except Exception as be:  # noqa: BLE001
                    return ("build_failed", name, str(be))
            return ("ok", name, None)
        except Exception as e:  # noqa: BLE001
            return ("failed", name, str(e))

    def record(status: str, name: str, err: str | None) -> None:
        if status == "skipped":
            results["skipped"].append(name)
            if on_progress:
                on_progress(f"skip {name}")
        elif status == "ok":
            results["ok"].append(name)
            if on_progress:
                on_progress(f"applied {name}")
        elif status == "built":
            results["ok"].append(name)
            results["built"].append(name)
            if on_progress:
                on_progress(f"applied+build {name}")
        elif status == "build_failed":
            results["ok"].append(name)
            results["build_failed"].append({"name": name, "error": err})
            if on_progress:
                on_progress(f"build failed {name}: {err}")
        else:
            results["failed"].append({"name": name, "error": err})
            if on_progress:
                on_progress(f"FAILED {name}: {err}")

    jobs = max(1, int(jobs))
    if jobs == 1:
        for it in items:
            record(*one(it))
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(one, it) for it in items]
            for fut in as_completed(futs):
                record(*fut.result())
    return results

