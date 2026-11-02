"""Tests for environment-based configuration (FreeCAD-independent)."""

from __future__ import annotations

import pytest

from freecad_mcp.config import (
    DEFAULT_SERVER_NAME,
    DEFAULT_TAIGA_DEVICE,
    DEFAULT_TAIGA_TIMEOUT_SECONDS,
    ENV_PREFIX,
    Settings,
    load_settings,
)


def test_defaults_when_environment_is_empty() -> None:
    assert load_settings({}) == Settings()


def test_taiga_defaults_are_safe() -> None:
    settings = load_settings({})

    assert settings.taiga_python is None
    assert settings.taiga_model is None
    assert settings.taiga_timeout_seconds == DEFAULT_TAIGA_TIMEOUT_SECONDS
    assert settings.taiga_device == DEFAULT_TAIGA_DEVICE


def test_taiga_values_are_read_from_environment() -> None:
    settings = load_settings(
        {
            f"{ENV_PREFIX}TAIGA_PYTHON": "  C:/taiga/python.exe  ",
            f"{ENV_PREFIX}TAIGA_MODEL": "  C:/models/taiga  ",
            f"{ENV_PREFIX}TAIGA_TIMEOUT_SECONDS": "45.5",
            f"{ENV_PREFIX}TAIGA_DEVICE": "  cuda  ",
        }
    )

    assert settings.taiga_python == "C:/taiga/python.exe"
    assert settings.taiga_model == "C:/models/taiga"
    assert settings.taiga_timeout_seconds == 45.5
    assert settings.taiga_device == "cuda"


def test_blank_taiga_paths_become_none() -> None:
    settings = load_settings(
        {
            f"{ENV_PREFIX}TAIGA_PYTHON": "   ",
            f"{ENV_PREFIX}TAIGA_MODEL": "   ",
        }
    )

    assert settings.taiga_python is None
    assert settings.taiga_model is None


def test_blank_taiga_device_falls_back_to_default() -> None:
    assert (
        load_settings({f"{ENV_PREFIX}TAIGA_DEVICE": "   "}).taiga_device
        == DEFAULT_TAIGA_DEVICE
    )


@pytest.mark.parametrize("value", ["0", "-1", "-0.5"])
def test_non_positive_taiga_timeout_raises(value: str) -> None:
    with pytest.raises(ValueError, match="TAIGA_TIMEOUT_SECONDS must be positive"):
        load_settings({f"{ENV_PREFIX}TAIGA_TIMEOUT_SECONDS": value})


def test_non_numeric_taiga_timeout_raises() -> None:
    with pytest.raises(ValueError, match="TAIGA_TIMEOUT_SECONDS must be a number"):
        load_settings({f"{ENV_PREFIX}TAIGA_TIMEOUT_SECONDS": "soon"})


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
