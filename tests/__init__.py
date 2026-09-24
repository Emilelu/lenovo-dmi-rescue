"""Test package.

Putting ``src`` on ``sys.path`` here means the suite runs from a clean checkout
with nothing installed::

    python -m unittest discover -v

That matters for this project -- the tool is meant to be run from a rescue USB
stick or a borrowed laptop, where ``pip install`` may not be an option.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
