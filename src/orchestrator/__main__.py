#!/usr/bin/env python3
"""orchestrator package entry — runs the main loop.

Two invocation forms are supported:

    python3 path/to/src/orchestrator        # bash dispatcher path
    python3 -m orchestrator                 # module mode

Both must reach `orchestrator.main.main()`. To make `from orchestrator.X
import Y` resolve in either case, we put `src/` on sys.path explicitly
before the package import. We also put `src/lib/` on sys.path so the
v0.5/v1.0/v1.2 bare-name imports (`import state`, `import locking`, ...)
keep working in submodules.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # .../src/orchestrator/
_SRC = _HERE.parent  # .../src/
_LIB = _SRC / "lib"

if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from orchestrator.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
