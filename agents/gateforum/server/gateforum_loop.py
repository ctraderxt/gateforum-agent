"""Keep the GateForum trading session alive.

Used by the heal timer. Delegates to gateforum_heal so there is one path.
"""
from __future__ import annotations

from gateforum_heal import main

if __name__ == "__main__":
    raise SystemExit(main())
