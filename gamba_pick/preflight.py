"""Detect-and-instruct preflight: figure out what's missing for the
customer's install before we touch the browser, and produce a
remediation list they can act on.

The preflight has three pieces:

- ``check_catalog(catalog_dir)`` — "do we have a catalog, and is it
  in a valid state to proceed?" Returns a ``PreflightAction`` telling
  the CLI what to do next (proceed with reference configs only,
  decrypt this .gpcat, or fail with a multi-catalog error).

- ``check_site_credentials(...)`` — for one site, "are the credentials
  / browser profile set up?" Returns a ``RemediationItem`` if the
  customer needs to do something, or ``None`` if the site is ready.

- ``run_preflight(...)`` (added in Task 15) — the aggregator that walks all
  configured sites and produces the full remediation list.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gamba_pick.catalog import CatalogState, discover_catalog


@dataclass(frozen=True)
class PreflightAction:
    """Result of ``check_catalog``. Tells the CLI what to do next.

    Possible kinds:
    - "info"     — print ``message``, continue with reference configs only
    - "decrypt"  — proceed to decrypt ``gpcat_path``
    - "error"    — print ``message``, exit non-zero
    """
    kind: str
    message: str = ""
    gpcat_path: Optional[Path] = None


@dataclass(frozen=True)
class RemediationItem:
    """One thing the customer needs to do before the next run will succeed."""
    site_id: str
    reason: str    # short human-readable summary (e.g. "needs credentials")
    action: str    # the exact instruction to print


def check_catalog(catalog_dir: Path) -> PreflightAction:
    state, path = discover_catalog(catalog_dir)
    if state is CatalogState.MULTI:
        return PreflightAction(
            kind="error",
            message=(
                "Multiple .gpcat files found in catalog/ — keep only the most "
                "recent one and re-run."
            ),
        )
    if state is CatalogState.ONE:
        assert path is not None
        return PreflightAction(kind="decrypt", gpcat_path=path)
    return PreflightAction(
        kind="info",
        message=(
            "No catalog found. Running with public reference configs only. "
            "Drop your .gpcat into catalog/ to enable your purchased sites."
        ),
    )


def check_site_credentials(
    *,
    site_id: str,
    auth: str,
    env: dict,
    profiles_dir: Path,
) -> Optional[RemediationItem]:
    """Return a RemediationItem describing what's missing, or None if ready.

    For ``auth="form"``: checks for <SITE_ID>_USERNAME and <SITE_ID>_PASSWORD.
    For ``auth="oauth"``: checks for a non-empty ``profiles/<site_id>/`` dir.
    """
    site_key = site_id.upper()
    if auth == "form":
        username_key = f"{site_key}_USERNAME"
        password_key = f"{site_key}_PASSWORD"
        missing = [k for k in (username_key, password_key) if not env.get(k)]
        if not missing:
            return None
        return RemediationItem(
            site_id=site_id,
            reason="missing credentials",
            action=(
                f"Edit picks.env and add: {username_key}=...; {password_key}=..."
            ),
        )
    if auth == "oauth":
        profile = profiles_dir / site_id
        if profile.exists() and profile.is_dir() and any(profile.iterdir()):
            return None
        return RemediationItem(
            site_id=site_id,
            reason="OAuth profile not bootstrapped",
            action=f"Bootstrap login for {site_id}: ./run.sh --setup {site_id}",
        )
    # Unknown auth value — not a remediation issue, just a configuration smell.
    # Don't block the run on this; the site script will fail with a clearer
    # error when it tries to log in.
    return None


def run_preflight(
    *,
    sites: list[dict],
    env: dict,
    profiles_dir: Path,
) -> list[RemediationItem]:
    """Walk every site and aggregate missing-setup items.

    ``sites`` is a list of dicts with ``id`` and ``auth`` keys (subset of
    Site fields — keeps preflight independent of the runner's dataclass).
    """
    items: list[RemediationItem] = []
    for site in sites:
        item = check_site_credentials(
            site_id=site["id"],
            auth=site.get("auth", "oauth"),
            env=env,
            profiles_dir=profiles_dir,
        )
        if item is not None:
            items.append(item)
    return items


def format_remediation(items: list[RemediationItem]) -> str:
    """Format the remediation list as a human-readable block.

    Empty list → empty string. Otherwise: a numbered list of actions
    followed by the "Setup incomplete: N step(s) remaining" trailer
    and the "When done, ..." nudge.
    """
    if not items:
        return ""
    lines = ["Setup is not complete. Take these steps and re-run:", ""]
    for i, item in enumerate(items, start=1):
        lines.append(f"  {i}. [{item.site_id}] {item.action}")
    lines.append("")
    lines.append(f"Setup incomplete: {len(items)} step(s) remaining.")
    lines.append("When done, run ./run.sh again to start your daily claim.")
    return "\n".join(lines)
