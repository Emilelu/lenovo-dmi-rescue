"""lenovo-dmi-rescue -- recover Lenovo DMI data after a bad BIOS flash.

The package is deliberately dependency free: everything works on a bare Python
3.9+ install, so the tool can be run from a rescue USB stick or a borrowed
laptop when the machine being repaired will not boot.
"""

from .blocks import LdbgBlock, LdbgRecord, LenvBlock, LenvEntry
from .locate import Layout, LenvStore, LocateError, locate
from .splice import SpliceError, SpliceResult, splice
from .scriptgen import FieldDiff, RestorePlan, generate, plan_restore

__version__ = "0.1.0"

__all__ = [
    "LdbgBlock",
    "LdbgRecord",
    "LenvBlock",
    "LenvEntry",
    "LenvStore",
    "Layout",
    "LocateError",
    "SpliceError",
    "SpliceResult",
    "FieldDiff",
    "RestorePlan",
    "generate",
    "locate",
    "plan_restore",
    "splice",
    "__version__",
]
