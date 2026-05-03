"""Backward-compat shim: re-exports ``gamba_pick.scrapling_pick``.

See ``casino.py`` (sibling shim) for rationale.
"""

import sys as _sys
from gamba_pick import scrapling_pick as _module

_sys.modules[__name__] = _module
