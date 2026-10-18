"""Allow ``python -m freecad_mcp`` to launch the stdio server."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
