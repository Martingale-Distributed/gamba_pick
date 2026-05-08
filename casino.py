"""Backward-compat shim: re-exports ``gamba_pick.casino`` as ``casino``.

Older external catalogs (e.g. casino-buddy-internal) still use the
pre-package-refactor ``from casino import ...`` convention. Subprocesses
launched by the runner get ``PYTHONPATH=<repo-root>``, so this file
satisfies their bare imports.

The replacement-via-sys.modules idiom means ``from casino import X``
returns the exact same X object as ``from gamba_pick.casino import X``
would — no duplicate module state.
"""

import sys as _sys
from gamba_pick import casino as _module

_sys.modules[__name__] = _module
