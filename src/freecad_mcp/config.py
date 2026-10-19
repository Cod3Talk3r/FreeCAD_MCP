"""Validated configuration for the freecad-mcp server.

Configuration is read from environment variables so no machine-specific paths or
secrets are committed to the repository. Every value has a safe default, and this
module must import without FreeCAD installed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

#: Prefix for all environment variables read by this project.
ENV_PREFIX = "FREECAD_MCP_"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

DEFAULT_SERVER_NAME = "freecad-mcp"

#: Default per-operation timeout for the FreeCAD bridge, in seconds.
DEFAULT_FREECAD_TIMEOUT_SECONDS = 60.0

#: Default controlled workspace directory (relative to the server working
#: directory) in which all FreeCAD documents are stored.
DEFAULT_WORKSPACE = "freecad-mcp-workspace"

#: Environment variable / setting name for the controlled workspace directory.
WORKSPACE_ENV = "FREECAD_MCP_WORKSPACE"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for the MCP server.

    Attributes:
        server_name: Name advertised to MCP clients.
        log_level: Logging level name.
        freecad_executable: Optional path to the FreeCAD console executable
            (``freecadcmd`` / ``FreeCADCmd``). ``None`` means "auto-discover".
            Kept as an override only; no machine-specific path is ever hard-coded.
        freecad_timeout_seconds: Maximum time to wait for one FreeCAD operation
            before it is aborted.
    """

    server_name: str = DEFAULT_SERVER_NAME
    log_level: LogLevel = "INFO"
    freecad_executable: str | None = None
    freecad_timeout_seconds: float = DEFAULT_FREECAD_TIMEOUT_SECONDS
    workspace: str = DEFAULT_WORKSPACE


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from environment variables.

    Args:
        environ: Mapping to read from. Defaults to :data:`os.environ`. Passing a
            mapping explicitly makes the function easy to test.

    Raises:
        ValueError: If a provided value is invalid (for example an unknown log
            level). Failing fast here keeps bad configuration out of the server.
    """
    env = os.environ if environ is None else environ

    raw_level = env.get(f"{ENV_PREFIX}LOG_LEVEL", "INFO").strip().upper()
    if raw_level not in LOG_LEVELS:
        allowed = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(f"{ENV_PREFIX}LOG_LEVEL must be one of: {allowed} (got {raw_level!r})")

    server_name = env.get(f"{ENV_PREFIX}SERVER_NAME", DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME

    raw_executable = env.get(f"{ENV_PREFIX}FREECAD_EXECUTABLE")
    freecad_executable = raw_executable.strip() if raw_executable else None

    raw_workspace = env.get(f"{ENV_PREFIX}{WORKSPACE_ENV}")
    workspace = raw_workspace.strip() if raw_workspace else DEFAULT_WORKSPACE

    timeout = DEFAULT_FREECAD_TIMEOUT_SECONDS
    raw_timeout = env.get(f"{ENV_PREFIX}TIMEOUT_SECONDS")
    if raw_timeout and raw_timeout.strip():
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(f"{ENV_PREFIX}TIMEOUT_SECONDS must be a number (got {raw_timeout!r})") from exc
        if timeout <= 0:
            raise ValueError(f"{ENV_PREFIX}TIMEOUT_SECONDS must be positive (got {timeout})")

    return Settings(
        server_name=server_name,
        log_level=raw_level,  # type: ignore[arg-type]  # validated against LOG_LEVELS above
        freecad_executable=freecad_executable or None,
        freecad_timeout_seconds=timeout,
        workspace=workspace,
    )
