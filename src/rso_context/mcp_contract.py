"""Stdlib MCP tool contracts. Importable without the MCP SDK."""

from __future__ import annotations

from pathlib import Path
import os
import functools
import re
import socket
import sys
import tempfile

from .scope import path_is_parent


PROTOCOL_ID = "rso-mcp-tools/v1"
SERVER_NAME = "rso-context"
SDK_REQUIREMENT = "mcp==2.2.0"
SDK_SPEC = "https://modelcontextprotocol.io/docs/learn/architecture"

# Launch-time only. Tools must not accept a database path or arbitrary file read.
LAUNCH_BINDINGS = ("db", "root", "strict_roots")

# Access modes. "open" accepts any bounded folder a tool call names, so one
# host entry serves every project on every host. "strict" accepts only folders
# under a launch --root (or the launch working directory when no --root).
ACCESS_OPEN = "open"
ACCESS_STRICT = "strict"

# Folders that hold one profile per account (C:\Users, /home, /Users).
PROFILE_CONTAINERS = {"users", "home"}

# Profile folders that hold credentials; nothing at any depth beneath them is a project.
SECRET_HOME_FOLDERS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker"}

_ADMIN_SHARE = re.compile(r"^\\\\([^\\]+)\\([A-Za-z])\$(?:\\(.*))?$", re.IGNORECASE)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "::1.ipv6-literal.net", "0--1.ipv6-literal.net"}


@functools.lru_cache(maxsize=1)
def _local_addresses() -> frozenset[str]:
    """This computer's own IP addresses, looked up once."""
    found: set[str] = set()
    try:
        name = socket.gethostname()
        found.update(socket.gethostbyname_ex(name)[2])
        found.update(info[4][0] for info in socket.getaddrinfo(name, None))
    except OSError:
        pass
    return frozenset(address.casefold() for address in found)


def _local_hosts() -> set[str]:
    """Every name or address this computer answers to in a UNC path, casefolded."""
    names = set(_LOOPBACK_HOSTS) | _local_addresses()
    found = [os.environ.get("COMPUTERNAME")]
    try:
        found.append(socket.gethostname())
    except OSError:
        pass
    for name in found:
        if name:
            names.add(name.casefold())
            names.add(name.casefold().split(".")[0])
    return names

UNBOUNDED_HOME_CHILDREN = {
    "desktop",
    "documents",
    "downloads",
    "onedrive",
}

TOOLS = (
    {
        "name": "rso_use",
        "writes": True,
        "required": ("path", "agent"),
        "description": (
            "Register and incrementally ingest a bounded workspace, then return a resume packet. "
            "This writes ingest state. path is any bounded project folder (launch --root only under --strict-roots). "
            "agent is attribution, not authentication."
        ),
    },
    {
        "name": "rso_query",
        "writes": True,
        "required": ("query", "path", "agent"),
        "description": (
            "Return a compact evidence packet (rso-mcp-packet/v1) for an already-registered project. "
            "This writes a run record and consumes run budget. "
            "path is the registered project folder. Database path is not a tool argument. "
            "Omitted spans are recovered with rso_expand, not by guessing."
        ),
    },
    {
        "name": "rso_check",
        "writes": True,
        "required": ("questions", "path", "agent"),
        "description": (
            "Answer 1-12 typed questions (claim, choice, value) from live ledger evidence "
            "and return rso-check/v1 with the spans behind each answer. No probabilities; "
            "a supported answer is not verification. Writes a run record and consumes one "
            "run-budget unit per call. path is the registered project folder."
        ),
    },
    {
        "name": "rso_resume",
        "writes": False,
        "required": ("path", "agent"),
        "description": (
            "Return a compact resume packet for an already-registered project. "
            "Does not ingest. path is the registered project folder."
        ),
    },
    {
        "name": "rso_explain",
        "writes": False,
        "required": ("packet_hash",),
        "description": (
            "Return a saved query packet from the launch-bound ledger by packet_hash. "
            "Read-only against that database."
        ),
    },
    {
        "name": "rso_expand",
        "writes": False,
        "required": ("ref",),
        "description": (
            "Retrieve omitted evidence by a stable expansion reference "
            "(project, source, expected hash, line range). "
            "Bounded and stale when the live file no longer matches. "
            "Not a generic path read."
        ),
    },
    {
        "name": "rso_validate",
        "writes": True,
        "required": ("claim", "validator", "result", "agent"),
        "description": (
            "Record a named validation for one claim (id, unique 8+ char id prefix, or exact text "
            "with path). result is verified, disputed or superseded. validator names who or what "
            "checked it (a person, test suite or command); agent is who records it. Only a named "
            "validation changes trust_state; observed claims stay unverified without one."
        ),
    },
)

TOOL_NAMES = tuple(item["name"] for item in TOOLS)


def tool_names() -> tuple[str, ...]:
    return TOOL_NAMES


def _account_home_paths() -> list[Path]:
    """The profile as the operating system records it, without environment variables.

    A host that replaces the child environment can drop USERPROFILE and HOME.
    The refusal must not disappear with them, or open access would accept the
    profile itself.
    """
    found: list[Path] = []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            # FOLDERID_Profile {5E6C858F-0E22-4760-9AFE-EA3317B67173}
            folder = _GUID(
                0x5E6C858F, 0x0E22, 0x4760,
                (ctypes.c_ubyte * 8)(0x9A, 0xFE, 0xEA, 0x33, 0x17, 0xB6, 0x71, 0x73),
            )
            buffer = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(buffer)) == 0:
                if buffer.value:
                    found.append(Path(buffer.value))
                ctypes.windll.ole32.CoTaskMemFree(buffer)
        except (AttributeError, OSError, ValueError):
            pass
    else:
        try:
            import pwd

            found.append(Path(pwd.getpwuid(os.getuid()).pw_dir))
        except (ImportError, KeyError, OSError):
            pass
    return found


def _home_paths() -> list[Path]:
    """Every profile directory this process can name, for the root refusal.

    Path.home() raises RuntimeError when the environment carries no home, which
    a host that replaces the child environment can produce. The environment
    variables are consulted as well so a partial environment still gets the
    refusal instead of silently losing it. A process that can name no home at
    all cannot recognise a profile, and the refusal has nothing to compare.
    """
    candidates: list[Path] = []
    try:
        candidates.append(Path.home())
    except RuntimeError:
        pass
    for name in ("USERPROFILE", "HOME"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value))
    drive = os.environ.get("HOMEDRIVE")
    tail = os.environ.get("HOMEPATH")
    if drive and tail:
        candidates.append(Path(drive + tail))
    candidates.extend(_account_home_paths())
    homes: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = resolve_bounded(candidate)
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        homes.append(resolved)
    return homes


def normalize_path_text(value: str | Path) -> str:
    r"""Fold Windows spellings of one folder into its drive-letter form.

    The extended-length, UNC and local administrative share spellings all name
    a folder the plain drive-letter rule already judges, so they must not dodge
    it. What stays a \\host\share path is a real network or device path. A
    no-op off Windows.
    """
    text = str(value)
    if os.name != "nt":
        return text
    text = text.replace("/", "\\")
    if text.upper().startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\") and re.match(r"[A-Za-z]:", text[4:6]):
        text = text[4:]
    match = _ADMIN_SHARE.match(text)
    if match and match.group(1).casefold() in _local_hosts():
        text = match.group(2) + ":\\" + (match.group(3) or "")
    return text


def resolve_bounded(value: str | Path) -> Path:
    """Path.expanduser().resolve() with Windows spellings normalized before and after."""
    resolved = Path(normalize_path_text(value)).expanduser().resolve()
    again = normalize_path_text(resolved)
    return resolved if again == str(resolved) else Path(again).resolve()


def _resolved_or_none(value: str | Path | None) -> Path | None:
    if not value:
        return None
    try:
        return resolve_bounded(value)
    except (OSError, RuntimeError):
        return None


def _system_directories() -> list[Path]:
    """Operating-system folders that are never a project, nor inside one."""
    names: list[str | None]
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or os.environ.get("windir")
        names = [
            system_root,
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramData"),
        ]
        # Fallbacks that do not depend on the environment a host passes down.
        drive = os.path.splitdrive(normalize_path_text(system_root or sys.executable or ""))[0] or "C:"
        for folder in ("Windows", "Program Files", "Program Files (x86)", "ProgramData"):
            candidate = drive + "\\" + folder
            if os.path.isdir(candidate):
                names.append(candidate)
    else:
        names = [
            "/bin", "/sbin", "/boot", "/dev", "/etc", "/proc", "/sys", "/System",
            "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64", "/usr/libexec", "/usr/share", "/usr/include",
            "/lib", "/lib64", "/run", "/var/log",
            "/Library", "/Applications", "/private/etc",
        ]
    found: list[Path] = []
    for name in names:
        resolved = _resolved_or_none(name)
        if resolved is not None and resolved not in found:
            found.append(resolved)
    return found


def _ledger_home() -> Path | None:
    try:
        from .config import default_home

        return _resolved_or_none(default_home())
    except Exception:
        return None


def _within(parent: Path, child: Path) -> bool:
    return child == parent or path_is_parent(parent, child)


def _key(path: Path) -> Path:
    """The default macOS disk is case-insensitive, so compare folded paths there."""
    return Path(str(path).casefold()) if sys.platform == "darwin" else path


def _below(parent: Path, child: Path) -> bool:
    return child != parent and child.is_relative_to(parent)


def _profile_child_refusal(parts: list[str], below_temp: bool = False) -> str | None:
    """Refusal for a folder below a profile, given its lower-cased parts under the profile."""
    if not parts:
        return None
    first = parts[0]
    if first in SECRET_HOME_FOLDERS or parts[:2] == [".config", "gcloud"]:
        return "a credentials folder"
    if first == "appdata":
        # Application data is off limits except below the temp folder, where projects get made.
        if below_temp or (len(parts) >= 4 and parts[1:3] == ["local", "temp"]):
            return None
        return "an application data folder"
    if first == "library" and sys.platform == "darwin" and len(parts) <= 2:
        return "an application data folder"
    if first.startswith("onedrive") and len(parts) == 2 and parts[1] in UNBOUNDED_HOME_CHILDREN:
        return "an unbounded folder"
    if len(parts) == 1:
        if first in UNBOUNDED_HOME_CHILDREN or first.startswith("onedrive"):
            return "an unbounded folder"
        if first.startswith("."):
            return "a hidden profile folder"
    return None


def _is_drive_mount(resolved: Path) -> bool:
    """POSIX only: a whole drive mounted where removable or foreign drives appear.

    Project folders are often mount points themselves (bind mounts, Docker
    volumes), so a mount point alone is not refused; one sitting directly in
    /mnt, /media, /run/media or /Volumes is a drive. A WSL drive, /mnt/<letter>,
    is refused even when it does not report as a mount.
    """
    if os.name == "nt":
        return False
    parts = resolved.parts
    if sys.platform.startswith("linux") and len(parts) == 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return True
    in_drive_location = (
        (len(parts) == 3 and parts[1] in ("mnt", "media", "Volumes"))
        or (len(parts) == 4 and parts[1] == "media")
        or (len(parts) == 5 and parts[1:3] == ("run", "media"))
    )
    return in_drive_location and os.path.ismount(resolved)


def _wsl_refusal(parts: tuple[str, ...]) -> str | None:
    """A Windows drive seen from WSL, /mnt/<letter>/..., judged by the Windows rules."""
    if len(parts) < 4 or parts[1] != "mnt" or len(parts[2]) != 1 or not parts[2].isalpha():
        return None
    rest = [part.casefold() for part in parts[3:]]
    if rest[0] == "users":
        if len(rest) == 1:
            return "a folder that contains a user profile"
        if len(rest) == 2:
            return "a user profile"
        return _profile_child_refusal(rest[2:])
    if rest[0] in ("windows", "program files", "program files (x86)", "programdata"):
        return "an operating-system folder"
    return None


def _mount_parents() -> set[str]:
    names = {"/mnt", "/media", "/Volumes", "/run/media"}
    return {name.casefold() for name in names} if sys.platform == "darwin" else names


def _unc_refusal(resolved: Path) -> str | None:
    """Refusal for a \\\\host\\share\\... path that normalization left as a UNC path."""
    pieces = [piece for piece in str(resolved)[2:].split("\\") if piece]
    host = pieces[0].casefold() if pieces else ""
    if host in (".", "?"):
        return "a network or device path"
    if host in _local_hosts():
        return "a network path to this computer"
    if len(pieces) >= 2 and pieces[1].endswith("$"):
        return "an administrative share"
    if len(pieces) < 3:
        return "a filesystem root"
    rest = [piece.casefold() for piece in pieces[2:]]
    for index, part in enumerate(rest):
        if part in PROFILE_CONTAINERS:
            after = rest[index + 1 :]
            if not after:
                return "a folder that contains a user profile"
            if len(after) == 1:
                return "a user profile"
            return _profile_child_refusal(after[1:])
    return None


def bounded_folder_refusal(
    resolved: Path, *, allow_network: bool = False, typed: str | Path | None = None
) -> str | None:
    """What makes a resolved folder too broad or too sensitive to serve, or None.

    This is the single bounded-folder rule for every MCP path, whether a launch
    --root, the launch working directory, or a folder a tool call names. It
    refuses a filesystem root, a user profile or any folder that contains one,
    an unbounded profile child (Desktop, Documents, Downloads, OneDrive), a
    hidden profile child such as .ssh, operating-system folders, and the
    ledger home. Windows spellings are normalized first. A folder below an
    ordinary share on another computer is allowed (a mapped drive resolves to
    one); this computer's own shares, administrative shares, device paths, a
    share root, and a remote copy of a profile are refused. allow_network and
    typed are kept so callers do not change; they no longer decide anything.
    """
    resolved = Path(normalize_path_text(resolved))
    if os.name == "nt" and str(resolved).startswith("\\\\"):
        return _unc_refusal(resolved)
    if resolved == Path(resolved.anchor) or resolved.parent == resolved or _is_drive_mount(resolved):
        return "a filesystem root"
    if os.name != "nt" and _key(resolved).as_posix() in _mount_parents():
        return "a folder that holds drive mounts"
    if sys.platform.startswith("linux"):
        what = _wsl_refusal(resolved.parts)
        if what:
            return what
    rc = _key(resolved)
    temp = _resolved_or_none(tempfile.gettempdir())
    below_temp = temp is not None and _below(_key(temp), rc)
    for home in _home_paths():
        hc = _key(home)
        if rc == hc:
            return "a user profile"
        container = hc.parent if hc.parent.name.casefold() in PROFILE_CONTAINERS else None
        if container is not None and rc.parent == container:
            # Another account's profile beside this one, such as C:\Users\Public.
            return "a user profile"
        if _below(rc, hc):
            return "a folder that contains a user profile"
        profile = None
        if _below(hc, rc):
            profile = hc
        elif container is not None and _below(container, rc):
            profile = container / rc.relative_to(container).parts[0]
        if profile is not None:
            what = _profile_child_refusal([part.casefold() for part in rc.relative_to(profile).parts], below_temp)
            if what:
                return what
    # /usr and /var themselves are refused; projects below them (/usr/src/app,
    # /var/lib/jenkins/workspace/x, macOS temp under /private/var) stay allowed.
    if os.name != "nt" and rc.as_posix() in ("/usr", "/var", "/private/var"):
        return "an operating-system folder"
    for system in _system_directories():
        if _within(_key(system), rc):
            return "an operating-system folder"
    ledger = _ledger_home()
    if ledger is not None and _within(ledger, resolved):
        return "the RSO ledger home"
    if resolved == _resolved_or_none(tempfile.gettempdir()):
        return "the system temp folder"
    return None


def assert_bounded_root(path: str | Path, *, allow_network: bool = False) -> Path:
    """Refuse a folder that is too broad or too sensitive to serve."""
    try:
        resolved = resolve_bounded(path)
    except OSError as error:
        raise ValueError(f"Cannot resolve MCP root: {path}") from error
    if not resolved.is_dir():
        raise ValueError(f"MCP root is not a directory: {resolved}")
    what = bounded_folder_refusal(resolved, allow_network=allow_network, typed=path)
    if what:
        raise ValueError(f"Refusing to bind {what} as an MCP root: {resolved}")
    return resolved


def bind_roots(roots: list[str] | tuple[str, ...], *, allow_network: bool = False) -> list[Path]:
    if not roots:
        raise ValueError("rso-context mcp requires one or more --root folders")
    bound: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = assert_bounded_root(root, allow_network=allow_network)
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        bound.append(resolved)
    return bound


def resolve_launch_roots(
    roots: list[str] | tuple[str, ...] | None,
    *,
    cwd: str | Path | None = None,
    strict: bool = True,
) -> list[Path]:
    """Launch roots, falling back to the folder the host started the server in.

    A stdio host usually spawns the server in the project the user opened, so
    with no --root that directory is bound and one config entry serves every
    project. Explicit --root values are always checked strictly. When strict is
    False, a working directory that cannot be a root (a desktop app launched
    from its install folder or the user profile) yields no launch roots instead
    of an error, so the server still starts; open access then takes each
    project folder from the tool call itself.
    """
    if roots:
        # A network path the user names with --root is theirs to bind.
        return bind_roots(roots, allow_network=True)
    if cwd is not None:
        candidate = Path(cwd)
    else:
        try:
            candidate = Path.cwd()
        except OSError as error:
            if not strict:
                return []
            raise ValueError(
                f"No --root was given and the working directory cannot be read: {error}"
            ) from error
    try:
        return bind_roots([str(candidate)])
    except ValueError as error:
        if not strict:
            return []
        raise ValueError(
            f"No --root was given and the working directory cannot be a launch root: {error}"
        ) from error


def path_is_within_root(root: str | Path, path: str | Path) -> bool:
    try:
        root_path = resolve_bounded(root)
        child = resolve_bounded(path)
    except OSError:
        return False
    if root_path == child:
        return True
    return path_is_parent(root_path, child)


def resolve_tool_path(path: str | Path, roots: list[Path]) -> Path:
    """Resolve a tool path and require it to stay inside a launch root."""
    if not str(path).strip():
        raise ValueError("path is required")
    try:
        resolved = resolve_bounded(path)
    except OSError as error:
        raise ValueError(f"Cannot resolve path: {path}") from error
    for root in roots:
        if path_is_within_root(root, resolved):
            return resolved
    allowed = ", ".join(str(root) for root in roots) or "none"
    raise ValueError(f"path is outside the server's allowed roots: {resolved} (allowed: {allowed})")


def resolve_workspace_path(
    path: str | Path,
    roots: list[Path],
    *,
    database_path: str | Path | None = None,
    access: str = ACCESS_STRICT,
) -> Path:
    """Workspace path for use/query/resume: a bounded directory, never the ledger file.

    Under strict access the path must sit under a launch root. Under open
    access a path outside every launch root is accepted when the folder itself
    passes the bounded-folder rule, which is the same bound a launch --root
    must meet.
    """
    if not str(path).strip():
        raise ValueError("path is required")
    try:
        resolved = resolve_bounded(path)
    except OSError as error:
        raise ValueError(f"Cannot resolve path: {path}") from error
    if database_path is not None:
        try:
            db = Path(database_path).expanduser().resolve()
        except OSError as error:
            raise ValueError(f"Cannot resolve launch database: {database_path}") from error
        if resolved == db:
            raise ValueError("path must not be the launch database")
    inside = any(path_is_within_root(root, resolved) for root in roots)
    if not inside and access != ACCESS_OPEN:
        resolve_tool_path(resolved, roots)
    if not resolved.is_dir():
        raise ValueError(f"path is not a directory: {resolved}")
    if not inside:
        what = bounded_folder_refusal(resolved, typed=path)
        if what:
            raise ValueError(
                f"Refusing to use {what} as a project folder: {resolved}. "
                "Pass the project folder itself, not a profile or system folder."
            )
    return resolved


def alias_is_visible(resolved_path: str | Path | None, roots: list[Path]) -> bool:
    if resolved_path is None or not str(resolved_path).strip():
        return False
    return any(path_is_within_root(root, resolved_path) for root in roots)


def restrict_packet_to_roots(
    packet: dict,
    roots: list[Path],
    visible_project_ids: set[str],
) -> dict:
    """Drop evidence, claims, and search_order entries outside launch roots."""
    restricted = dict(packet)

    def keep_source(item: dict) -> bool:
        resolved = item.get("resolved_path")
        if resolved is not None and str(resolved).strip():
            return alias_is_visible(resolved, roots)
        project_id = str(item.get("project_id") or item.get("id") or "")
        return bool(project_id) and project_id in visible_project_ids

    restricted["evidence"] = [item for item in packet.get("evidence") or [] if keep_source(item)]
    restricted["claims"] = [
        item
        for item in packet.get("claims") or []
        if str(item.get("project_id") or "") in visible_project_ids
    ]
    restricted["graph_nodes"] = [
        item
        for item in packet.get("graph_nodes") or []
        if str(item.get("project_id") or "") in visible_project_ids
    ]
    restricted["search_order"] = [
        item
        for item in packet.get("search_order") or []
        if str(item.get("id") or "") in visible_project_ids
    ]
    restricted["validations"] = [
        item
        for item in packet.get("validations") or []
        if not item.get("project_id") or str(item.get("project_id")) in visible_project_ids
    ]
    return restricted


def require_agent(agent: str) -> str:
    value = str(agent or "").strip()
    if not value:
        raise ValueError("agent is required and is attribution, not authentication")
    return value
