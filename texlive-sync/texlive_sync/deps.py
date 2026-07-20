"""Map tlpdb depends to RPM Requires and build order."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .tlpdb import TLPackage, is_skippable_depend


def map_depend(dep: str, quirks: dict[str, Any]) -> str | None:
    """
    Return an RPM capability for a tlpdb depend, or None to skip.

    Prefer virtual provides ``texlive(name)`` so a dep can be satisfied by a
    differently named package (e.g. a collection or the monolithic texlive
    SRPM). System packages stay as plain RPM names.
    """
    if is_skippable_depend(dep):
        return None
    system = quirks.get("system_packages") or {}
    if dep in system:
        return system[dep]
    if dep.endswith(".ARCH"):
        base = dep[: -len(".ARCH")]
        # Binary companions: texlive(name.bin) — provided by texlive-*.bin
        return f"texlive({base}.bin)"
    # collection-foo / scheme-bar / plain TL package name
    return f"texlive({dep})"


def rpm_requires(pkg: TLPackage, quirks: dict[str, Any]) -> list[str]:
    """Ordered unique Requires capabilities (without the Requires: prefix)."""
    seen: set[str] = set()
    out: list[str] = []
    self_cap = f"texlive({pkg.name})"
    for dep in pkg.depends:
        rpm = map_depend(dep, quirks)
        if rpm is None or rpm in seen:
            continue
        # never require the module's own texlive(name) capability.
        # Note: texlive(name.bin) is *not* skipped — for natives it comes from
        # texlive-binaries; for folded wrappers the same package Provides it
        # and RPM self-satisfies.
        if rpm == self_cap or rpm == pkg.rpm_name():
            continue
        seen.add(rpm)
        out.append(rpm)
    for extra in (quirks.get("extra_requires") or {}).get(pkg.name, []) or []:
        if extra not in seen:
            seen.add(extra)
            out.append(extra)
    return out


def topo_sort(
    packages: dict[str, TLPackage],
    quirks: dict[str, Any],
    only: set[str] | None = None,
) -> list[str]:
    """
    Topological order of TL package names by Requires edges.
    Unknown external deps are ignored for ordering.
    """
    names = set(packages)
    if only is not None:
        names &= only

    graph: dict[str, set[str]] = {n: set() for n in names}
    indeg: dict[str, int] = {n: 0 for n in names}

    for n in names:
        pkg = packages[n]
        for dep in pkg.depends:
            if is_skippable_depend(dep):
                continue
            dep_name = dep[: -len(".ARCH")] if dep.endswith(".ARCH") else dep
            if dep_name in system_names(quirks):
                continue
            if dep_name in names and dep_name != n:
                if n not in graph[dep_name]:
                    graph[dep_name].add(n)
                    indeg[n] += 1

    q = deque(sorted(n for n, d in indeg.items() if d == 0))
    order: list[str] = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in sorted(graph[u]):
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    # cycles or leftovers
    if len(order) < len(names):
        rest = sorted(names - set(order))
        order.extend(rest)
    return order


def system_names(quirks: dict[str, Any]) -> set[str]:
    return set((quirks.get("system_packages") or {}).keys())


def build_layers(
    packages: dict[str, TLPackage],
    quirks: dict[str, Any],
    only: set[str] | None = None,
) -> list[list[str]]:
    """Group topo order into parallel layers (same depth)."""
    order = topo_sort(packages, quirks, only)
    depth: dict[str, int] = {}
    names = set(order)
    for n in order:
        pkg = packages[n]
        d = 0
        for dep in pkg.depends:
            if is_skippable_depend(dep):
                continue
            dep_name = dep[: -len(".ARCH")] if dep.endswith(".ARCH") else dep
            if dep_name in names:
                d = max(d, depth.get(dep_name, 0) + 1)
        depth[n] = d
    layers: dict[int, list[str]] = defaultdict(list)
    for n in order:
        layers[depth[n]].append(n)
    return [layers[i] for i in sorted(layers)]
