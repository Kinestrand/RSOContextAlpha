from __future__ import annotations

import os
from collections import deque
from pathlib import Path

from .config import IGNORED_DIR_NAMES, PROJECT_MARKERS
from .db import Database
from .identity import register_project


def looks_like_project(path: Path) -> bool:
    try:
        names = {entry.name for entry in path.iterdir()}
    except OSError:
        return False
    if names.intersection(PROJECT_MARKERS):
        return True
    return any(name.lower().endswith((".sln", ".code-workspace")) for name in names)


def discover_project_paths(
    roots: list[Path],
    *,
    max_depth: int = 5,
    max_projects: int = 250,
) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    queue: deque[tuple[Path, int]] = deque((root.resolve(), 0) for root in roots if root.is_dir())

    while queue and len(found) < max_projects:
        current, depth = queue.popleft()
        key = os.path.normcase(str(current))
        if key in seen:
            continue
        seen.add(key)

        name = current.name.casefold()
        seed_container = depth == 0 and (
            name.startswith("onedrive")
            or name in {
                "codex",
                "documents",
                "downloads",
                "projects",
                "repos",
                "repositories",
                "onedrive",
                "onedrive - personal",
            }
        )
        if looks_like_project(current) and not seed_container:
            found.append(current)
            continue
        if depth >= max_depth:
            continue

        try:
            children = sorted(
                (item for item in current.iterdir() if item.is_dir()),
                key=lambda item: item.name.lower(),
            )
        except OSError:
            continue
        for child in children:
            if child.name.casefold() in IGNORED_DIR_NAMES or child.name.startswith("."):
                continue
            queue.append((child, depth + 1))
    return found


def discover_and_register(
    database: Database,
    roots: list[Path],
    *,
    max_depth: int = 5,
    max_projects: int = 250,
    agent: str = "observer",
) -> list[dict[str, object]]:
    registrations = []
    for path in discover_project_paths(roots, max_depth=max_depth, max_projects=max_projects):
        try:
            registrations.append(register_project(database, path, agent=agent))
        except (OSError, ValueError):
            continue
    return registrations
