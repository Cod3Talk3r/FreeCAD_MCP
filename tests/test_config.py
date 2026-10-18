"""Tests for environment-based configuration (FreeCAD-independent)."""

from __future__ import annotations

import pytest

from freecad_mcp.config import DEFAULT_SERVER_NAME, ENV_PREFIX, Settings, load_settings


def test_defaults_when_environment_is_empty() -> None:
    assert load_settings({}) == Settings()


def test_values_are_read_from_environment() -> None:
    settings = load_settings(
        {
            f"{ENV_PREFIX}SERVER_NAME": "custom-name",
            f"{ENV_PREFIX}LOG_LEVEL": "debug",
            f"{ENV_PREFIX}FREECAD_EXECUTABLE": "  /opt/freecad/freecadcmd  ",
        }
    )
    assert settings.server_name == "custom-name"
    assert settings.log_level == "DEBUG"
    assert settings.freecad_executable == "/opt/freecad/freecadcmd"


def test_blank_server_name_falls_back_to_default() -> None:
    assert load_settings({f"{ENV_PREFIX}SERVER_NAME": "   "}).server_name == DEFAULT_SERVER_NAME


def test_blank_executable_becomes_none() -> None:
    assert load_settings({f"{ENV_PREFIX}FREECAD_EXECUTABLE": "   "}).freecad_executable is None


def test_invalid_log_level_raises() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        load_settings({f"{ENV_PREFIX}LOG_LEVEL": "LOUD"})
