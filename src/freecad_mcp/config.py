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


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for the MCP server.

    Attributes:
        server_name: Name advertised to MCP clients.
        log_level: Logging level name.
        freecad_executable: Optional path to the FreeCAD console executable
            (``freecadcmd`` / ``FreeCADCmd``). ``None`` means "auto-discover",
            which the bridge implements in Milestone 2. Kept as an override only;
            no machine-specific path is ever hard-coded.
    """

    server_name: str = DEFAULT_SERVER_NAME
    log_level: LogLevel = "INFO"
    freecad_executable: str | None = None


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

    return Settings(
        server_name=server_name,
        log_level=raw_level,  # type: ignore[arg-type]  # validated against LOG_LEVELS above
        freecad_executable=freecad_executable or None,
    )
