# texlive-sync

Automated TeX Live packaging for OpenMandriva.

Generates RPM specs from upstream `texlive.tlpdb` + tlnet archives, stores
sources on ABF file-store, updates per-package GitHub repos, and can mass-build
on ABF. Specs are generated — put exceptions in `quirks.yaml` rather than
hand-editing.

Part of [OpenMandrivaSoftware/packaging-tools](https://github.com/OpenMandrivaSoftware/packaging-tools).
The older one-shot `package-texlive-module` shell script in the repo root is
legacy; prefer this toolkit for new work.

## Layout

```
texlive-sync/
  texlive_sync/     Python package (CLI: python3 -m texlive_sync)
  macros/           BuildSystem: texlive + bindir helpers (ship via texlive-tlpkg)
  quirks.yaml       Epochs, system renames, blocks
  scripts/
    mass-run.py     Mass apply + ABF build driver
    status.sh       Progress summary
  tests/
  spike-packages.txt
```

Runtime data (not in git):

| Path | Purpose | Env override |
|------|---------|--------------|
| `$HOME/texlive-abf-work` | Per-package git checkouts | `TEXLIVE_ABF_WORK` |
| `$HOME/texlive-sync-logs` | mass-state, apply/build logs | `TEXLIVE_SYNC_LOGDIR` |
| `texlive-sync/data/` | Cached `texlive.tlpdb` | (created by fetch) |

## Full update (typical)

```bash
cd packaging-tools/texlive-sync

# 1. Refresh upstream package database
python3 -m texlive_sync fetch-tlpdb

# 2. If macros/scripts under macros/ changed, ship texlive-tlpkg first
#    (abf store + bump Release + push OpenMandrivaAssociation/texlive-tlpkg)

# 3. Mass apply packages that changed, then start ABF builds
mkdir -p "$HOME/texlive-sync-logs"
nohup python3 -u scripts/mass-run.py \
  >> "$HOME/texlive-sync-logs/mass-run.out" 2>&1 &
echo $! > "$HOME/texlive-sync-logs/mass-run.pid"

# 4. Watch
scripts/status.sh
# or: tail -f $HOME/texlive-sync-logs/mass-run.out
```

`mass-run.py` skips packages whose work tree already matches the current
generator shape + tlpdb revision (`already_applied`). Re-run after a failure;
it resumes. When finished with remaining failures, run once more.

## Single package

```bash
python3 -m texlive_sync apply -p PACKAGENAME --build --work "$HOME/texlive-abf-work"
# force re-apply even if revision unchanged:
python3 -m texlive_sync apply -p PACKAGENAME --force --build --work "$HOME/texlive-abf-work"
python3 -m texlive_sync build -p PACKAGENAME
```

## Other commands

```bash
python3 -m texlive_sync generate --all          # specs only → out/
python3 -m texlive_sync generate -p acmart
python3 -m texlive_sync build-order [--package NAME]
python3 -m texlive_sync apply --all --jobs 8 --work "$HOME/texlive-abf-work"
```

## Version / release policy

| Case | Version | Release |
|------|---------|---------|
| Has `catalogue-version` | sanitized catalogue version | `<tl_revision>.1` |
| No catalogue-version | `<tl_revision>` | `1` |

Sources: `name.r<revision>.tar.xz` on CTAN tlnet. No `%{?dist}`.

## Binary policy

Never ship prebuilt native binaries not built from source.

| Kind | Packaging |
|------|-----------|
| Script/engine **wrappers** | Folded into noarch module: `%global tl_bin_links` + `Provides: texlive(name.bin)` |
| **Native** ELFs | Monorepo `texlive` SRPM (source tarball); virtual `texlive(name.bin)` from monorepo / texlive-binaries |
| Doc helper ELFs (e.g. opbible `mod2tex`) | Stripped at install by BuildSystem (magic-byte detect) |

Dependencies use virtual **`texlive(name)`** / **`texlive(name.bin)`** provides.

## Macros (texlive-tlpkg)

Ship files from `macros/` via the `texlive-tlpkg` package:

- `macros.buildsys.texlive` → `/usr/lib/rpm/macros.d/`
- `macros.texlive.rpm` / `texlive.macros` → path macros (`%{_texmfdistdir}`, …)
- `texlive-generate-files-specpart`, bindir maps/scripts → `/usr/lib/rpm/`

Rebuild **texlive-tlpkg** before mass-building modules if these change.

## Not covered by mass-run

- Monorepo engines package `OpenMandrivaAssociation/texlive` (source year bump by hand)
- Blocked tlpdb names in `quirks.yaml` (`00texlive.*`, `texlive.infra`, …)

## Tests

```bash
cd packaging-tools/texlive-sync
python3 -m pytest tests/ -q
```
