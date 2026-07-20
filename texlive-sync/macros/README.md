# RPM macros for BuildSystem: texlive

Declarative-build backend for generated `texlive-*` specs.

| File | Install as |
|------|------------|
| `macros.buildsys.texlive` | `/usr/lib/rpm/macros.d/macros.buildsys.texlive` |
| `texlive-generate-files-specpart` | `/usr/lib/rpm/texlive-generate-files-specpart` (executable) |
| `macros.texlive.rpm` | `/usr/lib/rpm/macros.d/macros.texlive` (path helpers; may already ship from `texlive-tlpkg`) |

## Packaging recommendation

Ship the macros + helper script from **`texlive-tlpkg`**. Generated specs only need:

```
BuildRequires: texlive-tlpkg
BuildSystem: texlive
```

## What the buildsystem implements

| Section | Behaviour |
|---------|-----------|
| prep | `%setup -q -c -aN…` for every `SourceN` (flat tlnet layout); flatten `RELOC/`; drop `tlpkg/` |
| conf | no-op |
| build | no-op |
| install | copy `tex/` `doc/` `source/` … into `%{buildroot}%{_texmfdistdir}`; run `texlive-generate-files-specpart` → `%{specpartsdir}/%{name}.files.specpart` |
| check | no-op |

Helpers: `%texlive_base_requires` (pulls in `texlive-tlpkg`).

Generated specs omit `%files` entirely — the install-time specpart is the
sole file list (empty for collection/scheme metapackages).

Directory ownership: every parent of installed files is emitted as `%dir`
(except `/usr` and `/usr/share`). Shared parents like
`%{_texmfdistdir}/tex/latex` are multi-owned on purpose so uninstall does
not leave empty orphans.

Do **not** call `texlive.post` — it is `#!/bin/true` (legacy stub). ls-R /
fmtutil refresh is handled by `texlive-tlpkg` filetriggers.
