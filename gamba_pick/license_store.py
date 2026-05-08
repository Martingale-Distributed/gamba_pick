"""Read/write of ``GAMBA_PICK_LICENSE`` in ``.env``.

Treats the env file as a flat KEY=VALUE file. We don't parse it as a
real shell file — we just look for / write the one variable we care
about, preserving the rest of the file. That's enough for our use case
(occasional one-time write from the license prompt).

Filename agnostic: callers pass the path explicitly. The CLI default is
``.env``; ``picks.env`` is honored as a one-release legacy fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

LICENSE_ENV_VAR = "GAMBA_PICK_LICENSE"


def read_license(env_path: Path) -> Optional[str]:
    """Return the license string from the env file, or None if absent.

    Strips surrounding quotes ("..." or '...') if present.
    """
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != LICENSE_ENV_VAR:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def write_license(env_path: Path, license_str: str) -> None:
    """Persist ``license_str`` as ``GAMBA_PICK_LICENSE`` in the env file.

    - If the file doesn't exist: create it.
    - If the variable already exists: replace that line in place.
    - Else: append the variable on a new trailing line.

    Does not modify any other lines.
    """
    new_line = f"{LICENSE_ENV_VAR}={license_str}"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    if not env_path.exists():
        env_path.write_text(new_line + "\n")
        return

    lines = env_path.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{LICENSE_ENV_VAR}="):
            lines[i] = new_line
            found = True
            break

    if not found:
        lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n")
