from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_MARKERS = {
    ".git",
    "AGENTS.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "Gemfile",
    "ProjectSettings",
}

TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".lua",
    ".md",
    ".mjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".tex",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

DOCUMENT_EXTENSIONS = {".docx"}

IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".rso-context",
    ".tox",
    ".venv",
    ".vs",
    ".vscode",
    "__pycache__",
    "backup",
    "backups",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "graft",
    "node_modules",
    "out",
    "output",
    "outputs",
    "private",
    "renders",
    "site-packages",
    "target",
    "temp",
    "tmp",
    "vendor",
    "venv",
}

IGNORED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "token.json",
}

SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}

SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:^|[._ -])(?:api[._ -]?keys?|access[._ -]?tokens?|auth|client[._ -]?secrets?|"
    r"credentials?|passwords?|passwd|private[._ -]?keys?|refresh[._ -]?tokens?|secrets?|tokens?)"
    r"(?:[._ -]|$)",
    re.IGNORECASE,
)


def is_sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    if name in IGNORED_FILE_NAMES or path.suffix.casefold() in SENSITIVE_SUFFIXES:
        return True
    if name.startswith(".env."):
        return True
    return bool(SENSITIVE_NAME_PATTERN.search(name))


def default_home() -> Path:
    configured = os.environ.get("RSO_CONTEXT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            return Path(user_profile) / ".rso-context"
    return Path.home() / ".local" / "share" / "rso-context-alpha"


def default_db_path() -> Path:
    return default_home() / "context.sqlite3"


def automatic_discovery_roots(cwd: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    candidates: list[Path] = [cwd or Path.cwd()]
    configured = os.environ.get("RSO_CONTEXT_ROOTS")
    if configured:
        candidates.extend(Path(value) for value in configured.split(os.pathsep) if value)

    # Only cwd and RSO_CONTEXT_ROOTS. Do not scan Documents/Downloads/OneDrive by default.
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 2 * 1024 * 1024
    max_files_per_project: int = 20_000
    max_projects: int = 250
    max_discovery_depth: int = 5
    chunk_characters: int = 1_600
    query_limit: int = 8
    query_token_budget: int = 4_000
    run_budget: int = 8
