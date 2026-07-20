# packaging-tools

OpenMandriva packaging automation tools.

## TeX Live (`texlive-sync/`)

Automated TeX Live module packaging from upstream `texlive.tlpdb`: generate
specs, store sources, update GitHub/ABF projects, mass-build.

```bash
cd texlive-sync
python3 -m texlive_sync fetch-tlpdb
python3 -u scripts/mass-run.py   # or nohup … → $HOME/texlive-sync-logs/
```

See [texlive-sync/README.md](texlive-sync/README.md).

The root script `package-texlive-module` is a legacy one-package helper;
prefer `texlive-sync` for ongoing updates.

## KDE / Plasma

Tools for auto-updating KDE packages (build lists, `auto-update`, `update`, …).

Note that when using the build lists, the Qt list requires that qt[ver]-qtbase
has bootstrap set before building other packages. The reason is that qtdoc
(part of qtbase) requires other parts of Qt to build (namely qtdoc and
qt-assistant). Everything else can be built without the qtdoc package with
the exception of qtlottie. Thus qtbase must be rebuilt without bootstrap
before building qtlottie.

## ABF API notes

Get list of packages in main:

```bash
curl -u "$APIKEY:$PASSWORD" \
  "https://abf.openmandriva.org/api/v1/repositories/10/projects?per_page=100&page=1"
```

`APIKEY` is the key at https://abf.openmandriva.org/settings/private
