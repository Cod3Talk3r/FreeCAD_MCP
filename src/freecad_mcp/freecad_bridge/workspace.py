"""Workspace and document locking for freecad-mcp.

Provides safe cross-process document isolation for the MCP server:
- Controlled workspace directory management
- Document ID validation (safe filenames, path-traversal resistant)
- Exclusive cross-process locking with PID-liveness detection
- Atomic save (temp + os.replace) to prevent partial writes
- Metadata sidecar for revision tracking (increment on successful save)

The lock implementation uses ctypes OpenProcess on Windows for reliable
process-liveness checks (os.kill(pid,0) is not dependable on Windows).
On other platforms, os.kill(pid, 0) is used as a best-effort check.

The module must import without FreeCAD installed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Windows PID-liveness detection (ctypes)
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes as _ct

    _KERNEL32 = _ct.windll.kernel32
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
else:
    _KERNEL32 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Document-ID validation
# ---------------------------------------------------------------------------

RESERVED_WINDOWS_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})


def validate_document_id(document_id: str) -> str:
    """Validate and normalise a document ID for use as a filename.

    Rules (all must pass; otherwise ValueError is raised):

    - Non-empty string after stripping.
    - Length <= 64 characters.
    - May contain only ASCII letters, digits, ``.``, ``_``, and ``-``.
    - Must not begin or end with a period (``.``).
    - Must not contain path separators or ``..`` components.
    - The basename (after splitting on ``.``) must not be a reserved
      Windows name (case-insensitive): CON, PRN, AUX, NUL, COM1..9, LPT1..9.
    - After any normalisation, the result must still be non-empty.

    Returns the validated, normalised document ID (lower-cased for the
    basename portion, interior periods and separators preserved).

    Raises:
        ValueError: If *document_id* fails any rule.
    """
    if not isinstance(document_id, str):
        raise ValueError(f"document_id must be str (got {type(document_id).__name__})")

    stripped = document_id.strip()
    if not stripped:
        raise ValueError("document_id must be non-empty after stripping")

    if len(stripped) > 64:
        raise ValueError(f"document_id too long ({len(stripped)} max 64)")

    # Allowed characters: ASCII letter, digit, dot, underscore, hyphen
    for ch in stripped:
        if not (ch.isalnum() or ch in ".-_"):
            raise ValueError(
                f"document_id may contain only ASCII letters, digits, '.', '_', '-'. "
                f"Found: {ch!r}"
            )

    # No leading/trailing period
    if stripped.startswith(".") or stripped.endswith("."):
        raise ValueError("document_id must not begin or end with '.'")

    # No path separators or ".." components
    if "/" in stripped or "\\" in stripped:
        raise ValueError("document_id must not contain path separators '/' or '\\'")

    # Check for ".." as a component
    parts = stripped.split(".")
    if ".." in parts:  # defensive; the char check already blocks embedded '/'
        raise ValueError("document_id must not contain '..'")

    # Basename check against reserved Windows names
    basename = stripped.split(".")[0]  # text before first dot is the "name"
    if basename.upper() in RESERVED_WINDOWS_NAMES:
        raise ValueError(
            f"document_id basename '{basename}' is a reserved Windows name."
        )

    # Normalise: lower-case the basename portion, preserve interior structure
    # E.g. "MyBox" -> "mybox", but "My.Box" stays "my.box"
    if "." in stripped:
        head, _, tail = stripped.partition(".")
        head = head.lower()
        rest = tail  # keep the rest as-is (including any inner dots/hyphens)
        # Re-assemble: lowercased head + "." + rest
        normalised = head + "." + rest if head else rest
    else:
        normalised = stripped.lower()

    if not normalised:
        raise ValueError("document_id normalised to empty string")

    return normalised


# ---------------------------------------------------------------------------
# Workspace management
# ---------------------------------------------------------------------------

DEFAULT_WORKSPACE = "freecad-mcp-workspace"
WORKSPACE_ENV = "FREECAD_MCP_WORKSPACE"


@dataclass(frozen=True, slots=True)
class Workspace:
    """A controlled workspace directory for freecad-mcp documents.

    All documents live under this directory.  Document IDs are validated
    and sanitised so that the resulting file paths never escape the workspace.

    Attributes:
        root: Absolute path to the workspace root directory.  Created if it
            does not exist when the :class:`Workspace` is constructed (via
            :func:`from_env` or manually).
        env_prefix: Environment-variable prefix used by :func:`from_env`.
    """

    root: Path

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def document_path(self, document_id: str) -> Path:
        """Return the ``.fcstd`` file path for *document_id* inside this workspace."""
        return self.root / f"{document_id}.fcstd"

    def lock_path(self, document_id: str) -> Path:
        """Return the lock-file path for *document_id* inside this workspace."""
        return self.root / f"{document_id}.lock"

    def meta_path(self, document_id: str) -> Path:
        """Return the metadata side-car path for *document_id* inside this workspace."""
        return self.root / f"{document_id}.meta.json"

    # ------------------------------------------------------------------
    # Factory from environment
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> Workspace:
        """Build a :class:`Workspace` from the ``FREECAD_MCP_WORKSPACE`` env var.

        Falls back to the project-default ``freecad-mcp-workspace`` relative
        to the current working directory.
        """
        raw = os.environ.get(WORKSPACE_ENV)
        if raw:
            root = Path(raw).resolve()
        else:
            root = Path.cwd() / DEFAULT_WORKSPACE
        return cls(root=root)

    # ------------------------------------------------------------------
    # Ensure the workspace root exists
    # ------------------------------------------------------------------

    def ensure_exists(self) -> None:
        """Create the workspace root directory if it does not exist."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Could not create workspace root {self.root}: {exc}") from exc


# ---------------------------------------------------------------------------
# Cross-process locking
# ---------------------------------------------------------------------------

#: Result of a :func:`try_acquire_lock` call.
LockStatus = dict[str, Any]


def _is_process_alive_windows(pid: int) -> bool:
    """Check whether a Windows process ID is still alive using ctypes.

    Returns ``True`` if the process exists and is running (STILL_ACTIVE),
    ``False`` if it has exited or the PID is invalid.
    """
    if _KERNEL32 is None:
        return True  # non-Windows fallback
    try:
        h = _KERNEL32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if h == 0:
            # GetLastError will be ERROR_ACCESS_DENIED (5) or similar;
            # treat as "possibly alive" to avoid false reclamation.
            return True
        ec = _ct.c_ulong()
        success = _KERNEL32.GetExitCodeProcess(h, _ct.byref(ec))
        _KERNEL32.CloseHandle(h)
        if not success:
            return True  # conservative
        return ec.value == _STILL_ACTIVE
    except Exception:
        return True  # conservative on any error


def _is_process_alive_unix(pid: int) -> bool:
    """Check whether a Unix process ID is still alive using os.kill(pid, 0).

    Returns ``True`` if the process exists, ``False`` if it does not
    (OSError with errno ESRCH).
    """
    import os as _os
    try:
        _os.kill(pid, 0)  # signal 0 is just a liveness check
        return True
    except OSError:
        return False


def _is_process_alive(pid: int) -> bool:
    """Cross-platform process-liveness check.

    On Windows uses ctypes ``OpenProcess`` + ``GetExitCodeProcess``.
    On other platforms uses ``os.kill(pid, 0)``.
    """
    if sys.platform == "win32":
        return _is_process_alive_windows(pid)
    return _is_process_alive_unix(pid)


def try_acquire_lock(
    lock_path: Path,
    *,
    pid: int,
    timeout_seconds: float = 30.0,
) -> LockStatus:
    """Try to acquire an exclusive lock on *lock_path*.

    The lock file contains JSON: ``{"document_id": "...", "owner_pid": N,
    "created_at": "ISO-8601", "purpose": "modeling_operation"}``.

    Behavior:

    1. If the lock file does **not** exist → create it atomically with
       ``os.open(..., O_CREAT | O_EXCL)`` and return ``{"status": "acquired", "lock": ...}``.

    2. If the lock file **exists** → read it and check the owner PID:

       a. If the recorded PID matches *pid* (the caller already owns it)
          → update ``created_at`` to now and return ``{"status": "reacquired"}``.

       b. If the recorded PID is different → check whether that owner PID
          is still alive using :func:`_is_process_alive`:

          - If the owner is **still alive** → return
            ``{"status": "locked", "owner_pid": recorded_pid,
            "lock_file": str(lock_path), "error": "document locked by alive process"}``.
            **Do not** reclaim the lock based on age alone; fail safely.

          - If the owner is **dead** (PID reclaimed) → **reclaim** the lock:
            overwrite the file with the caller's PID and return
            ``{"status": "acquired", "lock": ...}``.

    3. If the lock file is malformed (unparseable JSON) → return
       ``{"status": "error", "error": "lock file malformed", "lock_file": str(lock_path)}``.
       Do **not** attempt to reclaim; ownership is uncertain.

    The returned ``"lock"`` dict contains the lock-file path and the JSON
    content that was written, so the caller can later call
    :func:`release_lock`.

    Args:
        lock_path: Absolute or relative path to the lock file.
        pid: The caller's process ID (typically ``os.getpid()``).
        timeout_seconds: Ignored for this implementation (kept for API
            compatibility with possible future async semantics).

    Returns:
        A ``LockStatus`` dict as described above.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Attempt exclusive creation first
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "document_id": str(lock_path.parent.name if lock_path.parent.name else ""),
                    "owner_pid": pid,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "purpose": "freecad-mcp-modeling-operation",
                },
                fh,
            )
        return {"status": "acquired", "lock": {"path": str(lock_path), "written": True}}
    except FileExistsError:
        pass  # lock already exists; fall through to read-and-check
    except OSError as exc:
        return {"status": "error", "error": f"could not create lock file: {exc}", "lock_file": str(lock_path)}

    # Lock file exists — read and check
    try:
        raw = lock_path.read_text(encoding="utf-8")
        lock_data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "error": f"lock file malformed: {exc}",
            "lock_file": str(lock_path),
        }

    # Validate structure
    recorded_pid = lock_data.get("owner_pid")
    if recorded_pid is None:
        return {
            "status": "error",
            "error": "lock file missing 'owner_pid'",
            "lock_file": str(lock_path),
        }

    # Case 1: caller already owns the lock
    if recorded_pid == pid:
        # Update creation timestamp so the lock isn't stale due to age alone
        lock_data["created_at"] = datetime.now(timezone.utc).isoformat()
        try:
            lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
        except OSError:
            pass
        return {"status": "reacquired", "lock": {"path": str(lock_path), "written": True}}

    # Case 2: different owner — check liveness
    owner_alive = _is_process_alive(int(recorded_pid))

    if owner_alive:
        # Owner may still be alive → fail safely, never reclaim based on age
        return {
            "status": "locked",
            "owner_pid": int(recorded_pid),
            "lock_file": str(lock_path),
            "error": "document locked by alive process; fail safely",
        }

    # Case 3: owner is dead → reclaim the lock
    lock_data["owner_pid"] = pid
    lock_data["created_at"] = datetime.now(timezone.utc).isoformat()
    lock_data["reclaimed"] = True
    try:
        lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
    except OSError as exc:
        # Failed to write lock file → cannot acquire, report error
        return {
            "status": "error",
            "error": f"could not write lock file: {exc}",
            "lock_file": str(lock_path),
        }
    return {"status": "acquired", "lock": {"path": str(lock_path), "written": True, "reclaimed": True}}


def release_lock(lock_path: Path, pid: int) -> bool:
    """Release a lock previously acquired via :func:`try_acquire_lock`.

    Returns ``True`` if the lock file was deleted (we owned it), ``False``
    otherwise (e.g. lock was already gone, or we did not own it).
    """
    try:
        if not lock_path.is_file():
            return False
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if data.get("owner_pid") != pid:
            # Not our lock — do not delete; another process may be waiting
            return False
        lock_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Atomic save helper
# ---------------------------------------------------------------------------

def atomic_save(target_path: Path, write_content) -> Path:
    """Write *write_content* to *target_path* atomically.

    The content is first written to a temporary file in the same directory
    (guaranteed same filesystem for ``os.replace``), then ``os.replace`` is
    used to atomically swap the temp file into the target position.

    This ensures that readers never see a partially-written file: they will
    see either the old content or the new content, never an intermediate
    state.

    After the replace, the temporary file is removed (by ``os.replace``; the
    OS deletes the old directory entry).

    Args:
        target_path: The final destination path (will be created/overwritten).
        write_content: A callable ``(temp_path: Path) -> None`` that writes
            content to the temporary path.  It receives a ``Path`` pointing
            to a temp file in the same directory as *target_path*.

    Returns:
        *target_path* (for convenience).

    Raises:
        RuntimeError: If the atomic replace fails.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # Generate a unique temp name using PID + timestamp to avoid collisions
    tmp_name = f".fcstd.tmp_{os.getpid()}_{int(time.time() * 1000)}"
    tmp_path = target_path.parent / tmp_name

    try:
        write_content(tmp_path)
        # os.replace is atomic on the same filesystem
        os.replace(str(tmp_path), str(target_path))
        return target_path
    except BaseException:
        # Clean up temp file if it still exists
        try:
            if tmp_path.is_file():
                tmp_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Metadata side-car (revision tracking)
# ---------------------------------------------------------------------------

_META_SCHEMA: dict[str, Any] = {
    "revision": 0,
    "created_at": None,  # ISO-8601 string or None
    "last_saved_at": None,  # ISO-8601 string or None
    "last_modified_at": None,  # ISO-8601 string or None
    "document_id": None,
    "notes": "",  # free-form string for human notes
}


def read_meta(meta_path: Path) -> dict[str, Any]:
    """Read the metadata side-car from *meta_path*.

    Returns a dict conforming to ``_META_SCHEMA``.  If the file does not
    exist or contains invalid JSON, a default schema dict is returned so
    that callers can always rely on the presence of the ``"revision"`` key.
    """
    if not meta_path.is_file():
        return _META_SCHEMA.copy()

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        # Ensure required keys exist
        result = _META_SCHEMA.copy()
        result.update(data)
        return result
    except (json.JSONDecodeError, OSError):
        return _META_SCHEMA.copy()


def write_meta(meta_path: Path, meta: dict[str, Any]) -> None:
    """Write the metadata side-car *meta* to *meta_path*.

    The dict is first validated against the presence of required keys
    (``"revision"``).  Missing keys are filled from the default schema.
    """
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure required keys
    result = _META_SCHEMA.copy()
    result.update(meta)
    try:
        meta_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Could not write metadata to {meta_path}: {exc}") from exc


def meta_status(meta_path: Path) -> str:
    """Return the state of the metadata side-car.

    Returns one of:
      - "missing": no metadata file exists
      - "malformed": file exists but contains invalid JSON or lacks essential keys
      - "valid": file exists and contains valid metadata with a revision field
    """
    if not meta_path.is_file():
        return "missing"
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if "revision" in data and isinstance(data["revision"], (int, float)):
            return "valid"
        else:
            return "malformed"
    except (json.JSONDecodeError, OSError):
        return "malformed"


# ---------------------------------------------------------------------------
# High-level convenience: create-document workflow
# ---------------------------------------------------------------------------

def create_document(
    workspace: Workspace,
    *,
    document_id: str,
    pid: int | None = None,
) -> dict[str, Any]:
    """High-level helper: validate ID, acquire lock, create .fcstd, write meta.

    The caller (MCP tool handler) should:

    1. Validate *document_id* via ``workspace.validate_document_id``.
    2. Call :func:`try_acquire_lock` on the lock path.
    3. If acquired, run the FreeCAD operation (via the bridge).
    4. On success, call :func:`atomic_save` to write the .fcstd, then
       :func:`write_meta` to set revision to 1.
    5. Call :func:`release_lock`.

    This function orchestrates steps 1–2 and provides the return dict for
    the caller to decide on the next steps.

    Args:
        workspace: A :class:`Workspace` instance.
        document_id: The document identifier (will be validated).
        pid: Process ID for lock ownership.  Defaults to ``os.getpid()``.

    Returns:
        A dict with keys:
        - ``"status"``: one of ``"id_invalid"``, ``"lock_acquired"``,
          ``"lock_failed_alive"``, ``"lock_file_malformed"``.
        - ``"document_id"``: the (possibly normalised) ID.
        - ``"lock"``: the LockStatus dict from :func:`try_acquire_lock`.
        - ``"meta_before"``: the meta dict read before the operation.
    """
    pid = pid or os.getpid()

    # 1. Validate document ID
    try:
        normalised_id = validate_document_id(document_id)
    except ValueError as exc:
        return {
            "status": "id_invalid",
            "document_id": document_id,
            "error": str(exc),
            "lock": {"path": "", "written": False},
            "meta_before": _META_SCHEMA.copy(),
        }

    # 2. Acquire lock
    lock_path = workspace.lock_path(normalised_id)
    lock_status = try_acquire_lock(lock_path, pid=pid)

    # 3. Read existing meta (if any) before we proceed
    meta_before = read_meta(workspace.meta_path(normalised_id))

    return {
        "status": lock_status.get("status", "unknown"),
        "document_id": normalised_id,
        "lock": lock_status,
        "meta_before": meta_before,
    }