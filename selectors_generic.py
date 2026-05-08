"""Backward-compat shim: re-exports ``gamba_pick.selectors_generic``.

See ``casino.py`` (sibling shim) for rationale.
"""

import sys as _sys
from gamba_pick import selectors_generic as _module

_sys.modules[__name__] = _module
