"""Backward-compat shim: re-exports ``gamba_pick.scrapling_ext``.

See ``casino.py`` (sibling shim) for rationale.
"""

import sys as _sys
from gamba_pick import scrapling_ext as _module

_sys.modules[__name__] = _module
