from argparse import ArgumentParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, List, Tuple, Dict, Literal, Union
from playwright.sync_api import (
    Page,
    Locator,
    Error as PlaywrightError,
    ElementHandle,
    expect,
)
from urllib.parse import urlparse


import pyotp
import random
import os
import logging
from functools import lru_cache

# Generic candidate lists used as fallbacks when a site config doesn't
# pin a specific selector. Lives in its own module so we have a single
# source of truth across action factories — see selectors_generic.py
# for the philosophy (fail-loud-when-explicit, fall-back-when-unset).
from selectors_generic import (
    GOOGLE_OAUTH_BUTTON as _GENERIC_GOOGLE_OAUTH_BUTTON,
    MODAL_CLOSE_BUTTON as _GENERIC_MODAL_CLOSE_BUTTON,
    TURNSTILE_CHECKBOX_OFFSET as _TURNSTILE_CHECKBOX_OFFSET,
    TURNSTILE_WIDGET as _TURNSTILE_WIDGET_SELECTORS,
)


# Scrapling routes ``browser_backend="chrome"`` + ``stealth=True`` through
# patchright (a stealth-patched Playwright fork) whose error classes do
# NOT inherit from playwright's. ``except PlaywrightError`` alone misses
# them and lets timeouts/navigation errors bubble out of our handlers.
# This tuple is what we actually want to catch around any Page/Locator
# call, regardless of which backend scrapling chose at runtime.
#
# IMPORTANT: ``BrowserError`` is itself a tuple. Python rejects NESTED
# tuples in ``except`` clauses with ``TypeError: catching classes that
# do not inherit from BaseException is not allowed`` — meaning
# ``except (AssertionError, BrowserError):`` will raise at runtime even
# though it looks fine. Flatten with concatenation:
# ``except (AssertionError,) + BrowserError:``.
try:
    from patchright.sync_api import Error as _PatchrightError

    BrowserError: tuple = (PlaywrightError, _PatchrightError)
except ImportError:  # pragma: no cover — patchright is a scrapling dep
    BrowserError = (PlaywrightError,)  # type: ignore[assignment]

# Timeout-only flavor of ``BrowserError``. Useful for distinguishing a
# benign "selector didn't show up in time" (often a normal "feature not
# present" path — e.g. a daily-bonus modal that doesn't auto-pop on
# already-claimed days) from a real navigation/protocol error that
# happens to share the ``BrowserError`` parent. Catch this first, then
# the broader ``BrowserError`` for everything else.
from playwright.sync_api import TimeoutError as _PlaywrightTimeoutError

try:
    from patchright.sync_api import TimeoutError as _PatchrightTimeoutError

    BrowserTimeoutError: tuple = (
        _PlaywrightTimeoutError,
        _PatchrightTimeoutError,
    )
except ImportError:  # pragma: no cover — patchright is a scrapling dep
    BrowserTimeoutError = (_PlaywrightTimeoutError,)  # type: ignore[assignment]

# Default contant values
CLICK_TIMEOUT_MS = 5000  # Default timeout for click operations
MAX_CLICK_RETRIES = 3  # Maximum number of retry attempts for failed clicks
HANG_DETECTION_SECONDS = 30  # Seconds without balance change before detecting hang
MAX_KENO_ITERATIONS = 1000  # Maximum iterations in gambling loop as safety net

# When true, ``safe_click`` calls ``page.pause()`` (Playwright Inspector)
# on detected click-intercept so the user can step through interactively.
# Driven by ``GAMBA_PICK_PAUSE_ON_STUCK=1`` env var; runner.py propagates
# this from the ``--pause-on-stuck`` CLI flag into per-site subprocess
# env. Refuse to enable in headless mode (Inspector needs a display).
PAUSE_ON_STUCK = os.getenv("GAMBA_PICK_PAUSE_ON_STUCK", "0") == "1"


@lru_cache(1, typed=True)
def setup_logger():
    """
    Create and configure a logger with a standard format.

    :returns: logging.Logger: Configured logger instance
    """
    logger = logging.getLogger("gamba_pick")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Add handler to logger (if not already added)
    if not logger.handlers:
        logger.addHandler(console_handler)

    return logger


# This is the global logger used throughout the module
log = setup_logger()


@dataclass
class CasinoAccountState:
    """Represents the current state of a casino account.

    ``balances`` is keyed by the currency code declared in each
    ``Currency`` config (``SC`` for Sweeps Coins, ``GC`` for Gold
    Coins, ``FC`` for Fortune Wins's Fortune Coins, etc.). The
    ``__str__`` form is the canonical line the runner's regex
    parses, e.g.::

        SC: 3.34, GC: 43562260.00, VIP: None

    Currency codes are emitted in alphabetical order so a multi-
    currency site (e.g. Fortune Wins with FC + GC) produces a
    deterministic line shape.
    """

    balances: Dict[str, float] = field(default_factory=dict)
    vip_level: str = "None"  # VIP level (Bronze, Silver, Gold, Platinum, Diamond, etc.)
    vip_progress: Optional[float] = None  # Progress to next VIP level (0.0-1.0)

    @property
    def sweeps_coins(self) -> float:
        """Back-compat shortcut for ``balances['SC']``."""
        return self.balances.get("SC", 0.0)

    @property
    def gold_coins(self) -> float:
        """Back-compat shortcut for ``balances['GC']``."""
        return self.balances.get("GC", 0.0)

    def __str__(self) -> str:
        balance_pairs = ", ".join(
            f"{code}: {value:.2f}" for code, value in sorted(self.balances.items())
        )
        # Empty-balances case drops the leading ``<pairs>, `` segment
        # entirely (no awkward trailing comma), e.g. ``VIP: None``.
        # The runner's ``_RX_ACCOUNT_LINE`` makes that segment
        # optional and parses both shapes.
        prefix = f"{balance_pairs}, " if balance_pairs else ""
        return f"{prefix}VIP: {self.vip_level}"


class Currency:
    """Represents a currency with its associated selectors."""

    def __init__(
        self,
        name: str,
        code: str,
        selectors: List[str],
        is_active_selector: Optional[str] = None,
        activate_selector: Optional[str] = None,
    ):
        self.name = name
        self.code = code
        self.selectors = selectors
        self.is_active_selector = is_active_selector
        self.activate_selector = activate_selector


class CurrencyDisplayConfig:
    """Configuration for currency selectors."""

    def __init__(
        self,
        currencies: List[Currency],
        currency_toggle_dropdown_selector: Optional[str] = None,
        currency_toggle_switch_selector: Optional[str] = None,
        read_settle_ms: int = 0,
    ):
        """
        Args:
            currencies: Per-currency selector / activator configs.
            currency_toggle_dropdown_selector: Optional dropdown to open
                before reading balances (Stake.us-style wallet popover).
            currency_toggle_switch_selector: Optional shared toggle to
                cycle between currencies after each read.
            read_settle_ms: Extra wait after the union hydration check
                succeeds and before parsing values, to let count-up
                animations finish. Zula renders a JS-driven count-up on
                each lobby load that animates from a cached / starting
                value up to the API's current balance — without this
                wait we read mid-animation and get values that are a
                consistent fraction of the real total.
        """
        self.currencies = currencies
        self.currency_toggle_dropdown_selector = currency_toggle_dropdown_selector
        self.currency_toggle_switch_selector = currency_toggle_switch_selector
        self.read_settle_ms = read_settle_ms


close_selectors = [
    "#close",
    "div#close",
    "[aria-label='Close']",
    "button[aria-label='Close']",
    ".close",
]


def make_get_casino_account_state(
    currency_display_config: CurrencyDisplayConfig,
) -> Callable[[Page], CasinoAccountState]:
    """makes a function for getting the account state via a closure.

    Args:
        currency_display_config (CurrencyDisplayConfig): Configuration for currency selectors.
    Returns:
        Callable[[Page], CasinoAccountState]: A function that parses the casino account state from a
    """

    def get_casino_account_state(page: Page) -> CasinoAccountState:
        """Parse the casino account state from the page.

        Extracts Stake Cash balance, Gold Coins balance, and VIP level information.

        Args:
            page (Page): The Playwright Page object representing the casino page.

        Returns:
            CasinoAccountState: An object containing all parsed account information.
        """
        # Click to open the dropdown.
        if currency_display_config.currency_toggle_dropdown_selector:
            page.click(
                currency_display_config.currency_toggle_dropdown_selector,
                delay=gaussian_random_delay(),
            )

        # Hydration wait — same shape as simple_claim's union wait. Post-
        # OAuth lobby hydration can take 5-25s as React subscribes to
        # balance state (SpinQuest is the slowest of the working set).
        # Wait for ``attached`` rather than ``visible``: count-up
        # animation containers are sometimes styled with
        # ``visibility:hidden`` while their inner spans render the
        # digits, and Playwright's ``visible`` check returns False on
        # the parent. ``text_content()`` works on hidden elements
        # anyway — we just need them in the DOM.
        union_selectors: List[str] = []
        for currency in currency_display_config.currencies:
            union_selectors.extend(currency.selectors)
        if union_selectors:
            try:
                page.locator(", ".join(union_selectors)).first.wait_for(
                    state="attached", timeout=30000
                )
            except (AssertionError,) + BrowserError:
                log.warning(
                    "No currency selectors attached after 30s; balance read may be 0"
                )

        # Site-specific settle wait — let JS-driven count-up animations
        # finish before we read the value. Zula's lobby plays a count-up
        # from a starting/cached value up to the API balance; without
        # this wait we capture an intermediate value (consistently
        # ~30% of the final number on Camoufox).
        if currency_display_config.read_settle_ms:
            page.wait_for_timeout(currency_display_config.read_settle_ms)

        balances: Dict[str, float] = {}
        vip_level = "None"
        vip_progress = None

        # If any currency has its own ``activate_selector``, this site
        # uses the inline-toggle pattern (Sportzino: clicking the
        # inactive currency's own button activates it). In that case
        # we drive activation per-currency below and skip the shared
        # ``currency_toggle_switch_selector`` post-iteration click,
        # which would either be redundant or close the wrong thing.
        use_inline_activators = any(
            c.activate_selector for c in currency_display_config.currencies
        )

        # Try to parse Stake Cash balance
        for currency in currency_display_config.currencies:
            # Per-currency activator: click to make this currency
            # the active one before reading. Two patterns supported:
            #
            #   * **Idempotent activate** (Sportzino, FortuneWins):
            #     two side-by-side currency buttons; clicking the
            #     already-active button is a no-op. ``is_active_selector``
            #     can be left None and the click runs unconditionally.
            #   * **Toggle activate** (SpinQuest): one button that
            #     toggles between currencies on each click. Set
            #     ``is_active_selector`` to a marker that's only
            #     present when this currency is active so we skip
            #     the click when already in the right state.
            if currency.activate_selector:
                already_active = False
                if currency.is_active_selector:
                    try:
                        if page.locator(currency.is_active_selector).count() > 0:
                            already_active = True
                    except BrowserError:
                        pass

                if not already_active:
                    try:
                        page.click(
                            currency.activate_selector,
                            delay=gaussian_random_delay(),
                            timeout=5000,
                        )
                        # Count-up animation settles within ~500ms; give
                        # 800ms margin so the value span has the final
                        # number when we read it.
                        page.wait_for_timeout(800)
                    except BrowserError as e:
                        log.debug(
                            "activate_selector %s click failed: %s",
                            currency.activate_selector,
                            e,
                        )

            for selector in currency.selectors:
                try:
                    element_selector = page.locator(selector)
                    if element_selector.count() > 0:
                        raw = element_selector.first.text_content() or ""
                        # Strip currency-code prefix and thousands separators
                        # before float-parsing. Logs the raw text so a
                        # mis-targeted selector (returning "" or junk) is
                        # visible without a separate debug session.
                        cleaned = (
                            raw.strip()
                            .replace(currency.code, "")
                            .replace(",", "")
                            .strip()
                        )
                        # Logged at INFO so the runner's stderr_tail
                        # captures the actual raw text even on success.
                        # Useful for diagnosing wrong-value parses where
                        # the selector matches but the rendered content
                        # differs from what the user sees in their own
                        # browser (e.g. Camoufox vs Chrome rendering, or
                        # cached pre-API balances).
                        log.info(
                            "Currency %s selector %s: raw=%r cleaned=%r",
                            currency.code,
                            selector,
                            raw,
                            cleaned,
                        )
                        try:
                            n = float(cleaned)
                            log.info(f"Found {currency.name} balance: {n}")
                            balances[currency.code] = n
                            break
                        except ValueError:
                            continue
                except Exception as e:
                    log.debug(f"Selector {selector} failed for {currency.name}: {e}")
                    continue
            if (
                currency_display_config.currency_toggle_switch_selector
                and not use_inline_activators
            ):
                # Switch to next currency in dropdown
                page.click(
                    currency_display_config.currency_toggle_switch_selector,
                    delay=gaussian_random_delay(),
                )

        # Click again to close the dropdown
        if currency_display_config.currency_toggle_dropdown_selector:
            page.click(
                currency_display_config.currency_toggle_dropdown_selector,
                delay=gaussian_random_delay(),
            )

        return CasinoAccountState(
            balances=balances,
            vip_level=vip_level,
            vip_progress=vip_progress,
        )

    return get_casino_account_state


def _find_first_visible(page: Page, selector: str) -> Optional[Locator]:
    """Return the first *visible* match for ``selector``, or ``None``.

    ``Locator(...).first`` matches whichever element happens to come
    first in the DOM, without regard to visibility. That's a footgun
    for popup-dismissal flows on MUI/portal-mounted dialogs: hidden
    carcasses (closed dialogs left in the DOM with ``display: none``
    or ``visibility: hidden``) routinely appear before the live one,
    causing ``.first.is_visible()`` to return ``False`` and the
    dismiss loop to bail while a real popup sits unhandled.

    This helper walks the locator's full match set and returns the
    first match that ``Locator.is_visible()`` reports as visible.
    Returns ``None`` if there are no matches at all or none are
    visible — both cases callers typically treat as "nothing to do".
    """
    candidates = page.locator(selector)
    n = candidates.count()
    for i in range(n):
        candidate = candidates.nth(i)
        if candidate.is_visible():
            return candidate
    return None


def wait_and_remove(
    page: Page,
    selector: str,
    label: str,
    wait_timeout_ms: int = 5000,
) -> bool:
    """Wait briefly for a modal to mount, then JS-remove all matches.

    Use in place of ``make_dismiss_popup`` for overlays where
    Playwright's click-based dismiss is unreliable in patchright —
    e.g. close-button clicks fire but the React onClose handler
    doesn't propagate to actually unmount the modal, leaving a
    backdrop blocking subsequent clicks. JS removal sidesteps
    Playwright click semantics, navigation-orphan-promise risk
    (the same gotcha ``make_pre_login_click`` defends against), and
    React state-propagation timing entirely.

    Cost: doesn't fire the modal's React ``onClose`` handler, so
    React state is stale after removal. Fine for one-shot daily
    runs that close the browser at the end; not appropriate for
    long-lived sessions where the same modal might remount and
    confuse internal state.

    ``selector`` accepts a comma-separated list (Playwright's
    ``locator(...)`` and the underlying ``querySelectorAll`` both
    treat commas as CSS "or"), useful for modals that mount as
    multiple body-level siblings (e.g. dialog container + separate
    blur backdrop).

    Verbose by design: logs whether the element appeared, how many
    matches were removed, or whether nothing was visible to remove.
    Tune log levels at the framework level if this becomes noisy
    across many sites.

    Args:
        page: Playwright page.
        selector: CSS selector (comma-separated lists supported)
            for the element(s) to wait-for-then-remove.
        label: Short human-readable name for log messages
            (e.g. "cookie-consent", "welcome-bonus").
        wait_timeout_ms: How long to wait for the first match to
            become visible before giving up. Default 5000ms.

    Returns:
        True if at least one match was removed, False otherwise
        (timeout, error, or zero matches at removal time).
    """
    try:
        page.locator(selector).first.wait_for(
            state="visible", timeout=wait_timeout_ms
        )
    except BrowserError:
        log.info(
            "[wait_and_remove:%s] not visible within %dms; nothing to remove (%s)",
            label,
            wait_timeout_ms,
            selector,
        )
        return False
    try:
        n = page.evaluate(
            "(sel) => { const els = document.querySelectorAll(sel); "
            "els.forEach(el => el.remove()); return els.length; }",
            selector,
        )
    except BrowserError as e:
        log.warning(
            "[wait_and_remove:%s] JS-remove failed for %s: %s",
            label,
            selector,
            e,
        )
        return False
    if n > 0:
        log.info(
            "[wait_and_remove:%s] JS-removed %d match(es) of %s",
            label,
            n,
            selector,
        )
        return True
    log.info(
        "[wait_and_remove:%s] selector matched nothing at removal time (%s)",
        label,
        selector,
    )
    return False


def make_dismiss_popup(
    modal_selector: str,
    close_selector: Optional[str] = None,
    fallback_selector: Optional[str] = None,
    timeout_ms: int = 8000,
    name: Optional[str] = None,
) -> Callable[[Page], None]:
    """Build a callback that dismisses a single post-login popup.

    Wired into ``LoginConfig.post_login_callback``. Sites on the
    SLNGApp OAuth platform routinely auto-pop a daily-bonus / welcome
    / promo dialog right after login whose backdrop blocks subsequent
    header clicks; this is the standard way to clear it.

    Strategy (first that succeeds wins):

      1. Wait up to ``timeout_ms`` for ``modal_selector`` to become
         visible. If it doesn't appear, no-op cleanly (no popup
         today, already dismissed, etc.).
      2. Click ``close_selector`` if provided.
      3. Try each generic candidate in
         ``selectors_generic.MODAL_CLOSE_BUTTON`` (scoped *inside*
         the modal so we don't accidentally click an unrelated
         page-level close).
      4. Press Escape.
      5. Click ``fallback_selector`` if provided — useful for popups
         whose only CTA is something like "GO TO COIN STORE" that
         dismisses-as-side-effect.

    Args:
        modal_selector: CSS for the modal's root container. Whether
            the popup is "there" is determined by this becoming
            visible.
        close_selector: Site-specific close button (e.g.
            ``button.daily-bonus-dialog-close-button``). Optional;
            generic + Escape are tried regardless.
        fallback_selector: Last-resort click. Often a CTA that
            navigates somewhere benign, dismissing the popup as a
            side effect.
        timeout_ms: How long to wait for the modal to appear.
        name: Logged label so callers can tell which popup got
            dismissed when stacking multiple ``make_dismiss_popup``
            calls. Defaults to ``modal_selector``.
    """
    label = name or modal_selector

    def _wait_gone(page: Page) -> bool:
        """Confirm the popup is actually gone after a dismissal click.

        Returns True if the modal selector resolves to hidden within
        3s, False otherwise. Used as the success criterion so a click
        that didn't actually dismiss (wrong button, animation only)
        falls through to the next strategy instead of returning early.
        """
        try:
            page.wait_for_selector(modal_selector, state="hidden", timeout=3000)
            return True
        except BrowserError:
            return False

    def dismiss_popup(page: Page) -> None:
        try:
            page.wait_for_selector(
                modal_selector, state="visible", timeout=timeout_ms
            )
        except BrowserError:
            log.info("[popup:%s] not present after %dms; skipping", label, timeout_ms)
            return

        modal = page.locator(modal_selector).first

        # All three click sites below pass ``no_wait_after=True``.
        # Dismissal clicks routinely fire side-effect requests (cookie-
        # consent service writes, modal-close telemetry, fallback CTA
        # navigations). Playwright's default click semantics arm a
        # "wait for navigation" promise after each click; if no nav
        # actually fires (it was just a fetch) the promise orphans on
        # the Node side and unhandled-rejection-crashes the driver
        # several seconds later — typically mid-flow during a
        # downstream wait, with a misleading 3000ms TimeoutError. Same
        # gotcha ``make_pre_login_click`` documents and defends against.
        # 1. Site-specific close button (fastest path when it works).
        if close_selector:
            close_btn = page.locator(close_selector).first
            try:
                if close_btn.count() > 0 and close_btn.is_visible():
                    close_btn.click(
                        delay=gaussian_random_delay(),
                        timeout=3000,
                        no_wait_after=True,
                    )
                    if _wait_gone(page):
                        log.info(
                            "[popup:%s] dismissed via close_selector %s",
                            label,
                            close_selector,
                        )
                        return
            except BrowserError:
                pass

        # 2. Generic close buttons, scoped inside the modal so we
        # don't click an unrelated page-level close.
        for sel in _GENERIC_MODAL_CLOSE_BUTTON:
            try:
                cand = modal.locator(sel).first
                if cand.count() > 0 and cand.is_visible():
                    cand.click(
                        delay=gaussian_random_delay(),
                        timeout=3000,
                        no_wait_after=True,
                    )
                    if _wait_gone(page):
                        log.info("[popup:%s] dismissed via generic %s", label, sel)
                        return
            except BrowserError:
                continue

        # 3. Escape key.
        try:
            page.keyboard.press("Escape")
            if _wait_gone(page):
                log.info("[popup:%s] dismissed via Escape", label)
                return
        except BrowserError:
            pass

        # 4. Last-resort fallback. Often a CTA whose side effect is
        # navigation (e.g. "GO TO COIN STORE"); the modal disappears
        # because the page transitions, not because anything closes.
        # Don't gate on _wait_gone here — caller knows the side effect.
        if fallback_selector:
            fb = page.locator(fallback_selector).first
            try:
                if fb.count() > 0 and fb.is_visible():
                    fb.click(
                        delay=gaussian_random_delay(),
                        timeout=3000,
                        no_wait_after=True,
                    )
                    log.info(
                        "[popup:%s] dismissed via fallback %s",
                        label,
                        fallback_selector,
                    )
                    return
            except BrowserError:
                pass

        log.warning(
            "[popup:%s] visible but no dismissal strategy worked; "
            "downstream actions may fail",
            label,
        )

    return dismiss_popup


def make_dismiss_popup_stack(
    modal_selector: str,
    close_selector: str,
    max_iterations: int = 5,
    name: Optional[str] = None,
    content_filter: Optional[str] = None,
    initial_wait_ms: int = 0,
) -> Callable[[Page], None]:
    """Build a callback that dismisses a *stack* of popups in a loop.

    Sites occasionally pile up multiple modal dialogs back-to-back
    after login (Modo: store popup → "Claim your offer!"; some
    sites add a third welcome / promo dialog on top). Single-popup
    ``make_dismiss_popup`` clears one and returns; this helper
    keeps clicking ``close_selector`` until no element matching
    ``modal_selector`` is visible, or until ``max_iterations`` is
    hit.

    Strategy each iteration:

      1. Locate the first visible match for ``modal_selector``.
         If none, exit cleanly (everything is dismissed).
      2. If ``content_filter`` is set and the modal's text doesn't
         contain it (case-insensitive), exit without clicking —
         the popup is something we don't want to dismiss (e.g. a
         live daily-claim modal sharing the slot).
      3. Click the first visible match for ``close_selector``.
      4. Brief settle wait so the dialog finishes its dismissal
         animation before the next iteration probes.

    Args:
        modal_selector: CSS for any popup container to look for —
            typically a framework-specific class (e.g.
            ``.MuiDialog-root:not([aria-hidden="true"])`` for
            Material-UI sites). Match-and-visible determines
            "popup is here", just like ``make_dismiss_popup``.
        close_selector: CSS for the close button to click. Often
            a structural pattern (``.MuiDialog-root button[aria-label="close"]``)
            so the same selector works across re-rendered popup
            instances.
        max_iterations: Bound on how many popups to dismiss.
            Default 5 — generous enough for current site flows,
            tight enough that a misconfigured selector can't loop
            forever.
        name: Logged label so callers can tell which stack was
            being dismissed. Defaults to ``modal_selector``.
        content_filter: Optional case-insensitive substring required
            in the modal body before we'll dismiss it. Designed for
            sites where one selector slot hosts both a wanted dialog
            (e.g. live daily-claim modal) and unwanted ones (e.g. a
            coin-store upsell): ``"buy now"`` targets the upsell
            without stomping on the claim modal. ``None`` (default)
            preserves the original "dismiss everything" behavior.
        initial_wait_ms: First-iteration grace period — wait up to
            this long for ``modal_selector`` to appear before treating
            "no popup" as done. Designed for sites that lazy-render
            their offer modals a second or two after login completes
            (Pulsz / PulszBingo's coin-store upsell). ``0`` (default)
            preserves the original instant-check behavior.

    Returns:
        ``Callable[[Page], None]`` — wired into
        ``LoginConfig.post_login_callback`` for sites with stacked
        post-login popups.
    """
    label = name or modal_selector
    filter_lower = content_filter.lower() if content_filter else None

    def dismiss_popup_stack(page: Page) -> None:
        if initial_wait_ms > 0:
            try:
                page.locator(modal_selector).first.wait_for(
                    state="visible", timeout=initial_wait_ms
                )
            except BrowserError:
                log.info(
                    "[popup-stack:%s] no popup appeared within %dms",
                    label,
                    initial_wait_ms,
                )
                return
        for i in range(max_iterations):
            try:
                # Scan all matches for a *visible* one rather than
                # assuming the first DOM match is the open popup —
                # MUI / portal-mounted dialogs frequently leave hidden
                # carcasses in the DOM alongside the live one, and
                # ``.first`` would short-circuit on those and bail out
                # while a visible popup sits unhandled. See
                # ``_find_first_visible`` for the iteration semantics.
                modal = _find_first_visible(page, modal_selector)
                if modal is None:
                    if i == 0:
                        log.info(
                            "[popup-stack:%s] no popup to dismiss", label
                        )
                    else:
                        log.info(
                            "[popup-stack:%s] dismissed %d popup(s)",
                            label,
                            i,
                        )
                    return
                if filter_lower is not None:
                    body = (modal.text_content() or "").lower()
                    if filter_lower not in body:
                        log.info(
                            "[popup-stack:%s] visible popup doesn't match "
                            "filter %r — leaving it (%d dismissed so far)",
                            label,
                            content_filter,
                            i,
                        )
                        return
                close_btn = _find_first_visible(page, close_selector)
                if close_btn is None:
                    log.warning(
                        "[popup-stack:%s] popup visible but close "
                        "selector %s not — bailing after %d iter(s)",
                        label,
                        close_selector,
                        i,
                    )
                    return
                close_btn.click(
                    delay=gaussian_random_delay(), timeout=5000
                )
                # Settle window for the dialog's leave animation
                # before we probe again on the next iteration.
                page.wait_for_timeout(500)
            except BrowserError as e:
                log.warning(
                    "[popup-stack:%s] iter %d failed: %s",
                    label,
                    i,
                    e,
                )
                return
        log.info(
            "[popup-stack:%s] hit max_iterations=%d; leaving any "
            "remaining popup",
            label,
            max_iterations,
        )

    return dismiss_popup_stack


def make_handle_google_one_tap_popup(
    close_selectors: List[str],
) -> Callable[[Page], None]:
    """Generator for handling the google one tap popup

    close
    """
    _ = close_selectors

    def handle_google_one_tap_popup(page: Page) -> None:
        """Handle and close Google One Tap popup if it appears.
        Args:
            page (Page): The Playwright page object.
        """
        # Try to close the Google One Tap popup if it appears
        try:
            # Wait a bit for the Google iframe to load
            page.wait_for_timeout(1500)

            # Check if the Google One Tap container exists
            google_container = page.locator("div#credential_picker_container")
            if google_container.count() > 0:
                log.info("Google One Tap popup detected, attempting to close it")

                # Get the iframe using frame_locator
                iframe = page.frame_locator("div#credential_picker_container >> iframe")

                # Try to find and click the close button inside the iframe
                clicked = False
                for selector in close_selectors:
                    try:
                        # Check if element exists and click it
                        close_button = iframe.locator(selector)
                        if close_button.count() > 0:
                            close_button.click(
                                timeout=3000, delay=gaussian_random_delay()
                            )
                            log.info(f"Closed Google popup using selector: {selector}")
                            clicked = True
                            break
                    except Exception as e:
                        log.debug(f"Selector {selector} failed: {e}")
                        continue

                if not clicked:
                    log.warning("Could not find close button in iframe")
            else:
                log.info("No Google One Tap popup detected")
        except Exception as e:
            log.warning("Could not close Google popup (critical): %s", str(e))
            # Do raise - this is not optional, login doesn't work otherwise
            raise e

    return handle_google_one_tap_popup


def make_grant_geolocation_permission(
    origin: Optional[str] = None,
    reload_after: bool = True,
) -> Callable[[Page], None]:
    """Make a page action that grants geolocation permission WITHOUT overriding coords.

    For the "legitimate user on their own laptop" case: we want the site to
    receive the browser's real, naturally-derived location (from Camoufox's
    `geoip=True` mode, system geoclue, or whatever the underlying Firefox
    provider resolves). Overriding coords via `set_geolocation` would defeat
    that by forcing Playwright's synthetic value into `navigator.geolocation`,
    creating a mismatch with IP-level and WebRTC-level location signals that
    compliance services like GeoComply cross-check.

    This helper only pre-authorizes the permission (so the blocking prompt
    doesn't appear) and reloads the page so the site re-queries with the
    permission now granted.

    Args:
        origin: optional origin scope for the permission (e.g.
            "https://example.com"). If None, granted context-wide.
        reload_after: reload so the site picks up the now-granted
            permission. Leave True unless the caller is orchestrating
            their own reload.
    """

    def grant_geolocation_permission(page: Page) -> None:
        try:
            context = page.context
            perms_kwargs = {"origin": origin} if origin else {}
            context.grant_permissions(["geolocation"], **perms_kwargs)
            log.info(
                "Geolocation permission granted%s (coords will flow from browser's native provider)",
                f" for origin {origin}" if origin else "",
            )
            if reload_after:
                page.reload()
                wait_for_load_all_safe(page)
        except Exception as e:
            log.warning("Could not grant geolocation permission: %s", str(e))

    return grant_geolocation_permission


def make_set_geolocation(
    latitude: float,
    longitude: float,
    accuracy: float = 100.0,
    origin: Optional[str] = None,
    reload_after: bool = True,
) -> Callable[[Page], None]:
    """Make a page action that grants geolocation permission and sets coords.

    Sweepstakes casinos geo-gate by US state. Playwright browser contexts
    start with no geolocation and no permission, so the site's first
    `navigator.geolocation` call either blocks on a permission prompt or
    fails outright and the site reports "outside allowed jurisdiction."
    This helper pre-authorizes the permission and fixes the context's
    location, then (by default) reloads the page so the site picks up the
    new coordinates without the prompt ever firing.

    Args:
        latitude, longitude: coordinates to report.
        accuracy: reported accuracy in meters (100m is typical for IP-geo).
        origin: optional origin to scope the permission grant to (e.g.
            "https://spinquest.com"). If None, granted context-wide.
        reload_after: reload the page after setting; leave True unless the
            caller wants to defer the reload for their own orchestration.
    """

    def set_geolocation(page: Page) -> None:
        try:
            context = page.context
            perms_kwargs = {"origin": origin} if origin else {}
            context.grant_permissions(["geolocation"], **perms_kwargs)
            context.set_geolocation(
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy": accuracy,
                }
            )
            log.info(
                "Geolocation granted + set to (%.6f, %.6f, +/-%.0fm)",
                latitude,
                longitude,
                accuracy,
            )
            if reload_after:
                page.reload()
                wait_for_load_all_safe(page)
        except Exception as e:
            log.warning("Could not configure geolocation: %s", str(e))

    return set_geolocation


def wait_for_load_all_safe(
    page: Page, timeout: int = 500, full_load_timeout: int = 10000
) -> None:
    """Wait for the page to be fully loaded with error handling.

    Args:
        page: The Playwright page object.
        timeout: Max wait (ms) for the `networkidle` state. Short by design —
            treated as "give it up to this long to settle, then give up."
        full_load_timeout: Max wait (ms) for the `load` and `domcontentloaded`
            states. Previously unbounded (Playwright default = 30s each), which
            turned a stalled post-login navigation into a ~60s apparent hang.
            A bounded wait surfaces the issue faster; the caller sees a
            warning line and continues.
    """
    try:
        page.wait_for_load_state("load", timeout=full_load_timeout)
        page.wait_for_load_state("domcontentloaded", timeout=full_load_timeout)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except BrowserError as _:
            # Sometimes networkidle doesn't happen, ignore.
            pass
    except BrowserError as e:
        log.warning("Page did not fully load within timeout: %s", str(e))


def make_modal_tab_button(
    # modal selector, usually wallet button
    modal_selector='button[data-testid="wallet"], button[data-analytics="global-navbar-wallet-button"]',
    # tab selector, usually daily bonus button — Optional for sites
    # whose claim modal opens directly to the daily-bonus view (e.g.
    # YayCasino's coin-store modal that has no tabs).
    tab_selector: Optional[str] = 'button[data-testid="dailyBonus"]',
    # button selector, usually claim button
    btn_selector="button.justify-center:nth-child(4)",
    # close modal selector
    close_btn_selector='button[data-testid="modal-close"]',
    # How long to wait for ``btn_selector`` to become clickable after
    # the modal-open click. Most flows have the claim button visible
    # within a second; the default 5000ms covers UI animation slack.
    # Override for flows where the claim CTA only renders after a
    # multi-second animation — e.g. PulszBingo's "Wheel of Winners",
    # which spins for ~6-8s before "GET MY COINS" appears.
    btn_visibility_timeout_ms: int = 5000,
    # Per-site already-claimed marker. See MTBClaimConfig.
    already_claimed_selector: Optional[str] = None,
) -> Callable[[Page], None]:
    """Generator for claiming daily bonus via modal, tab, button pattern.
    Args:
        modal_selector (str): Selector for the modal open button.
        tab_selector (Optional[str]): Selector for the tab button
            inside the modal. ``None`` for sites whose claim modal
            opens directly on the daily-bonus view.
        btn_selector (str): Selector for the claim button.
        close_btn_selector (str): Selector for the modal close button.
        btn_visibility_timeout_ms (int): Wait budget for ``btn_selector``
            to become clickable. Bump for flows where the CTA is gated
            on a multi-second animation.
        already_claimed_selector (Optional[str]): If set, checked after
            modal+tab clicks but before waiting for ``btn_selector``.
            When matched (visible), logs "Daily bonus already claimed."
            and exits cleanly. Used for sites that signal claimed-state
            via a replacement element (e.g. a countdown timer) rather
            than disabling the Claim button or hiding the modal trigger.
    Returns:
        Callable[[Page], None]: A function that performs the daily bonus claim action on the given page.
    """
    log.debug(
        "selectors: %s, %s, %s, %s (btn_timeout=%dms)",
        modal_selector,
        tab_selector,
        btn_selector,
        close_btn_selector,
        btn_visibility_timeout_ms,
    )

    def claim_daily_bonus(page: Page) -> None:
        """Clicks to claim the daily bonus if available.
        Args:
            page (Page): The Playwright page object.

        Returns:
        """

        # Pre-check: if the trigger modal/element isn't visible, the
        # daily bonus is already claimed for today. Most sites suppress
        # the trigger (Modo's Daily Bonus card flips text to "Next:
        # <countdown>"; PulszBingo's Wheel of Winners modal stops
        # auto-popping). Treat a *timeout* (selector never appeared)
        # as a normal "already claimed" path. Other ``BrowserError``s
        # (navigation crashes, protocol errors, invalid selectors)
        # surface as warnings and fall through so the subsequent click
        # exposes the real failure rather than masking it as
        # ``already_claimed``. The timeout matches the click below so
        # a slow-rendering UI doesn't get misclassified.
        try:
            page.locator(modal_selector).first.wait_for(
                state="visible", timeout=5000
            )
        except BrowserTimeoutError:
            log.info("Daily bonus already claimed.")
            return
        except BrowserError as e:
            log.warning(
                "Unexpected error while pre-checking daily bonus modal "
                "visibility (%s) — proceeding to click and letting any "
                "real failure surface there.",
                e,
            )

        try:
            # Modal-open click (button.buttonBuy / wallet button / etc.)
            # — the canonical "stuck" site for this flow. ``safe_click``
            # runs a stuck-detection precheck and logs structured
            # diagnostics naming the intercepting element if anything
            # is on top, instead of burning the full Playwright
            # actionability-retry budget on a "<X> intercepts pointer
            # events" loop.
            if not safe_click(
                page,
                modal_selector,
                timeout=5000,
                max_retries=1,
            ):
                log.error(
                    "Daily bonus claim failed: modal trigger %s "
                    "(see [click_failed] / [stuck] log lines above for "
                    "the categorized reason).",
                    modal_selector,
                )
                return
            if tab_selector:
                if not safe_click(
                    page,
                    tab_selector,
                    timeout=5000,
                    max_retries=1,
                ):
                    log.error(
                        "Daily bonus claim failed: tab %s "
                        "(see [click_failed] / [stuck] log lines above for "
                        "the categorized reason).",
                        tab_selector,
                    )
                    return
            # Wait for EITHER the claim button OR the per-site
            # already-claimed marker to mount. Single wait avoids the
            # tab-content render race that bit a previous attempt
            # (``is_visible()`` returned False because the tab's
            # contents hadn't rendered yet, even though they would
            # have within ~500ms).
            #
            # Three detection paths for already-claimed state:
            #   1. ``modal_selector`` not visible (Modo, PulszBingo) —
            #      handled by the pre-check at the top of this fn.
            #   2. ``btn_selector`` is_disabled() (yay, americanluck,
            #      stake) — handled below after this wait.
            #   3. ``already_claimed_selector`` matches (shuffle's
            #      ``TimeRemain`` countdown that REPLACES the Claim
            #      button) — handled below right after this wait.
            wait_selector = (
                f"{already_claimed_selector}, {btn_selector}"
                if already_claimed_selector
                else btn_selector
            )
            try:
                page.wait_for_selector(
                    wait_selector,
                    state="attached",
                    timeout=btn_visibility_timeout_ms,
                )
            except BrowserError:
                log.error(
                    "[click_failed:%s] reason=claim_button_not_found "
                    "(neither claim button nor already-claimed marker "
                    "matched within %dms after modal+tab clicks; site's "
                    "claim surface may have changed — consider setting "
                    "or updating ``already_claimed_selector`` on the "
                    "MTBClaimConfig)",
                    btn_selector,
                    btn_visibility_timeout_ms,
                )
                if PAUSE_ON_STUCK:
                    log.info(
                        "[click_failed] PAUSE_ON_STUCK set; opening "
                        "Playwright Inspector. Click 'Resume' to "
                        "continue (or close to abort)."
                    )
                    try:
                        page.pause()
                    except BrowserError as e:
                        log.warning(
                            "[click_failed] page.pause() failed (no "
                            "display? running headless?): %s",
                            e,
                        )
                return

            # Already-claimed marker takes precedence: if visible,
            # we're done — log and return without trying to click
            # a button that may not exist.
            if already_claimed_selector:
                try:
                    if page.locator(already_claimed_selector).first.is_visible():
                        log.info("Daily bonus already claimed.")
                        return
                except BrowserError:
                    # Marker evaluation hiccuped — fall through to
                    # the claim-button path rather than masking a
                    # real failure.
                    pass
            claim_btn = page.locator(btn_selector)
            if claim_btn.is_disabled():
                log.info("Daily bonus already claimed.")
            else:
                if not safe_click(
                    page,
                    btn_selector,
                    timeout=btn_visibility_timeout_ms,
                    max_retries=1,
                ):
                    log.error(
                        "Daily bonus claim failed: claim button %s "
                        "(see [click_failed] / [stuck] log lines above for "
                        "the categorized reason).",
                        btn_selector,
                    )
                    return
                wait_for_load_all_safe(page, timeout=3000)
                # Canonical success line for runner.parse_outcome — every
                # claim factory should emit some form of "Daily bonus
                # claimed." so the runner can categorize without per-flow
                # vocabulary.
                log.info("Daily bonus claimed.")
        except BrowserError as e:
            log.error("Exception occurred while claiming daily bonus: %s", str(e))
        finally:
            # Close the wallet modal if it's still open
            try:
                close_btn = page.locator(close_btn_selector)
                if close_btn.count() > 0:
                    close_btn.click(delay=gaussian_random_delay(), timeout=10000)
            except BrowserError:
                pass

    return claim_daily_bonus


# MTB
#
# Modal,    Tab, Button; (Button)
# Click,  Click,  Click;  (Click)
#  Open, Switch,  Claim;   (Exit)
#
# Interface Objects
# User Interaction
# Affect Effected
#
def make_login_action_factory(
    username_selector: str,
    password_selector: str,
    login_submit_selector: str,
    totp_code_selector: Optional[str] = None,
    totp_submit_selector: Optional[str] = None,
    pre_login_form_callback: Optional[
        Callable[[Page], None]
    ] = make_handle_google_one_tap_popup(close_selectors),
    post_login_form_callback: Optional[Callable[[Page], None]] = None,
) -> Callable[[str, str, Optional[str]], Callable[[Page], None]]:
    """Generator for creating a login action factory.
    Args:
        username_selector (str): Selector for the username input field.
        password_selector (str): Selector for the password input field.
        login_submit_selector (str): Selector for the login submit button.
        totp_code_selector (str): Selector for the TOTP code input field.
        pre_login_form_callback (Optional[Callable[[Page], None]]): Optional callback for handling
            any pre-login forms or popups.
        post_login_form_callback (Optional[Callable[[Page], None]]): Optional callback for handling
            any post-login forms.
    Returns:
        Callable[[str, str, Optional[str]], None]: A function that creates a login action.
    """

    def login_action_factory(
        username: str, password: str, totp_secret: Optional[str]
    ) -> Callable[[Page], None]:
        """Create a login page action.

        Args:
            username (str): Username for login.
            password (str): Password for login.
            totp_secret (Optional[str]): TOTP secret for 2FA, if applicable.

        Returns:
            Callable[[Page], None]: A function that performs the login action on the given page.
        """

        def login_action(page: Page):
            # Initial page load handling popups / etc
            if pre_login_form_callback:
                pre_login_form_callback(page)

            # Fill in login form and submit
            page.fill(username_selector, username)
            page.fill(password_selector, password)

            # The submit button on SLNGApp-platform /login pages is gated
            # by Cloudflare Turnstile — it stays HTML-disabled until
            # ``cf-turnstile-response`` has a populated token. Without
            # this wait, ``page.click`` finds the locator but spins for
            # the full timeout waiting for it to become enabled, then
            # fails with "element is not enabled". Our solver clicks
            # the checkbox if invisible auto-pass doesn't fire within
            # the grace window, so this resolves inside ~10s typically.
            wait_for_turnstile(page, timeout=30000)

            try:
                page.click(
                    login_submit_selector, delay=gaussian_random_delay(), timeout=10000
                )
            except Exception as e:
                log.error("Error during login: %s", str(e))
                raise e

            # Wait for navigation to complete
            wait_for_load_all_safe(page)

            # Optional post-login form handling
            if post_login_form_callback:
                post_login_form_callback(page)

            # Handle TOTP 2FA if applicable
            if totp_secret and totp_code_selector and totp_submit_selector:
                try:
                    page.wait_for_selector(
                        totp_code_selector, state="visible", timeout=10000
                    )
                    totp_code = pyotp.TOTP(totp_secret).now()
                    page.fill(totp_code_selector, totp_code)
                    page.click(totp_submit_selector, delay=gaussian_random_delay())
                except Exception as e:
                    log.error("Error during TOTP entry: %s", str(e))
                    raise e

            # Wait for navigation to complete
            wait_for_load_all_safe(page)

        return login_action

    return login_action_factory


def highlight_element_handle(element: ElementHandle):
    """Highlight an element on the page for debugging purposes.

    Args:
        element: The Playwright ElementHandle object
    """
    try:
        element.evaluate(
            """(element) => {
                element.style.border = '3px solid red';
                setTimeout(() => { element.style.border = ''; }, 10000);
            }"""
        )
        log.info("Highlighted element with selector: %s", element)
    except BrowserError as e:
        log.error("Error highlighting element: %s", str(e))


def highlight_element(page: Page, selector: str):
    """Highlight an element on the page for debugging purposes.

    Args:
        page: The Playwright Page object
        selector: The CSS selector of the element to highlight
    """
    try:
        element: Locator = page.locator(selector).first
        if element.count() > 0:
            element.evaluate(
                """(element) => {
                    element.style.border = '3px solid red';
                    setTimeout(() => { element.style.border = ''; }, 10000);
                }"""
            )
            log.info("Highlighted element with selector: %s", selector)
        else:
            log.warning("No element found to highlight with selector: %s", selector)
    except BrowserError as e:
        log.error("Error highlighting element: %s", str(e))


# Detection selectors for Cloudflare Turnstile. The widget injects its
# response input asynchronously after the CF script evaluates, so we probe
# multiple signals — the hidden response input, the challenge iframe, the
# standard `cf-turnstile` container, any element with a `data-sitekey`
# attribute, and the Zula-style wrapper class that hosts the widget.
_TURNSTILE_DETECT_JS = """() => {
  return !!(
    document.querySelector('input[name="cf-turnstile-response"]') ||
    document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
    document.querySelector('div.cf-turnstile') ||
    document.querySelector('[data-sitekey]') ||
    document.querySelector('div.login-turnstile') ||
    document.querySelector('.login-form-content-turnstile')
  );
}"""

# Solve signal: a populated response input anywhere in the main frame.
# Real tokens are ~500-800 chars; >10 filters out empty / placeholder values.
_TURNSTILE_SOLVED_JS = """() => {
  const el = document.querySelector('input[name="cf-turnstile-response"]');
  return !!(el && el.value && el.value.length > 10);
}"""

# Substring markers in page HTML that distinguish between the three
# Cloudflare-managed challenge flavors. Only set on pages where CF
# fronts the whole site (IUAM-style "Just a moment..." pages and the
# managed/interactive challenge pages). Sites that just embed a
# Turnstile widget for their own verification (Sportzino, Zula) won't
# have these — the embedded path falls through to the script-tag check.
_TURNSTILE_CTYPE_MARKERS = (
    ("non-interactive", "cType: 'non-interactive'"),
    ("managed", "cType: 'managed'"),
    ("interactive", "cType: 'interactive'"),
)


def _detect_turnstile_kind(page: Page) -> Optional[str]:
    """Classify the Turnstile flavor on the current page.

    Returns one of ``"non-interactive"``, ``"managed"``, ``"interactive"``,
    ``"embedded"``, or ``None`` (no Turnstile). The first three indicate
    a Cloudflare-fronted page (IUAM / managed-challenge); ``"embedded"``
    means the site is just hosting a Turnstile widget. Mirrors scrapling's
    ``_detect_cloudflare`` but reads the live DOM instead of a snapshot,
    which avoids the timing race where the widget script hasn't mounted
    yet when scrapling's 500ms wait fires.
    """
    try:
        content = page.content() or ""
    except BrowserError:
        content = ""
    for kind, marker in _TURNSTILE_CTYPE_MARKERS:
        if marker in content:
            return kind
    if "challenges.cloudflare.com/turnstile/v" in content:
        return "embedded"
    # Fallback: DOM probe in case the widget mounted via JS injection
    # (some sites build the script tag dynamically and it doesn't appear
    # in the initial HTML snapshot).
    try:
        if page.evaluate(_TURNSTILE_DETECT_JS):
            return "embedded"
    except BrowserError:
        pass
    return None


def _find_turnstile_widget(page: Page) -> Optional[Locator]:
    """Locate a visible Turnstile widget container on the page.

    Tries a list of common selectors in priority order and returns the
    first visible match. Returns None if no widget is visible — the
    caller should treat that as "challenge already gone or never rendered".
    """
    for selector in _TURNSTILE_WIDGET_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0 and loc.is_visible():
                log.debug("Found Turnstile widget via selector: %s", selector)
                return loc
        except BrowserError:
            continue
    return None


def _click_turnstile_checkbox(page: Page, widget: Locator) -> bool:
    """Click the visible checkbox inside a located Turnstile widget.

    The widget content lives in a cross-origin iframe we can't address
    directly, so we click via page coordinates: the widget's bounding
    box plus a fixed offset where the checkbox sits in the stock theme.
    The ``delay=60`` mirrors scrapling's solver and gives the widget's
    JS handler time to register a real-looking mousedown→mouseup pair.

    Returns True if the click was issued, False if the widget has no
    bounding box (offscreen / display:none after our scroll).
    """
    try:
        widget.scroll_into_view_if_needed(timeout=3000)
    except BrowserError:
        pass
    page.wait_for_timeout(gaussian_random_delay(300))  # Settle after scroll.

    box = widget.bounding_box()
    if box is None:
        log.warning("Turnstile widget has no bounding box; cannot click")
        return False

    cx = box["x"] + _TURNSTILE_CHECKBOX_OFFSET[0]
    cy = box["y"] + _TURNSTILE_CHECKBOX_OFFSET[1]
    log.info("Clicking Turnstile checkbox at (%d, %d)", cx, cy)
    page.mouse.click(cx, cy, delay=60, button="left")
    return True


def wait_for_turnstile(
    page: Page,
    timeout: int = 30000,
    auto_click_after_ms: Optional[int] = 5000,
) -> bool:
    """Wait for (and optionally actively solve) a Cloudflare Turnstile challenge.

    Detects Turnstile by probing several DOM signals plus the page-content
    ``cType`` markers that distinguish CF-fronted pages from sites that
    just embed a widget. If none of those signals appear within a short
    grace window the page is assumed not to use Turnstile and the function
    returns immediately.

    Solve flow:

      1. If the response token is already populated, return True.
      2. For ``non-interactive`` (the IUAM "Just a moment..." page), poll
         until the title clears.
      3. For ``embedded`` / ``managed`` / ``interactive`` widgets, wait up
         to ``auto_click_after_ms`` for the invisible auto-pass.
      4. If still unsolved, locate the widget container and click on the
         visible checkbox via page coordinates. The standard widget
         resolves within a few seconds of the click.
      5. Continue waiting for the response token until the overall
         ``timeout`` elapses.

    Pass ``auto_click_after_ms=None`` to disable the active click and
    behave like the legacy passive waiter — useful in ``--setup`` mode
    where a human is driving and we don't want to race them.

    Args:
        page: Playwright page.
        timeout: Total ms to wait for resolution (covers both the
            auto-pass grace window and any post-click settle).
        auto_click_after_ms: Grace window for invisible auto-pass before
            we click the widget ourselves. ``None`` disables the click.

    Returns:
        True if Turnstile resolved (or wasn't present). False if it was
        present but didn't resolve in time.
    """
    # First pass: quick probe for any Turnstile signal.
    detected = page.evaluate(_TURNSTILE_DETECT_JS)
    if not detected:
        # Widgets are often injected asynchronously after DOMContentLoaded.
        # Give Cloudflare's script a chance to mount before giving up.
        try:
            page.wait_for_function(_TURNSTILE_DETECT_JS, timeout=5000)
            detected = True
        except BrowserError:
            detected = False

    if not detected:
        log.debug("No Turnstile detected on page")
        return True

    # Fast path: widget is already solved by the time we look.
    if page.evaluate(_TURNSTILE_SOLVED_JS):
        log.debug("Turnstile already solved")
        return True

    kind = _detect_turnstile_kind(page) or "embedded"
    log.info("Cloudflare Turnstile detected (kind=%s)", kind)

    # IUAM-style "Just a moment..." doesn't have a clickable checkbox —
    # CF's own JS resolves it once the browser passes its checks. We
    # just poll for the title to change.
    if kind == "non-interactive":
        try:
            page.wait_for_function(
                "() => !document.title.includes('Just a moment')",
                timeout=timeout,
            )
            log.info("Turnstile (non-interactive) cleared")
            return True
        except BrowserError:
            log.warning("Non-interactive Turnstile did not clear in %dms", timeout)
            return False

    # Embedded / managed / interactive: try invisible auto-pass first.
    grace = auto_click_after_ms if auto_click_after_ms is not None else timeout
    grace = max(0, min(grace, timeout))
    if grace > 0:
        try:
            log.info("Waiting up to %dms for Turnstile invisible auto-pass...", grace)
            page.wait_for_function(_TURNSTILE_SOLVED_JS, timeout=grace)
            log.info("Turnstile auto-passed")
            return True
        except BrowserError:
            pass

    # No auto-pass within the grace window — actively click the widget,
    # unless caller disabled the click.
    if auto_click_after_ms is None:
        log.info("Auto-pass disabled; waiting passively for token...")
    else:
        widget = _find_turnstile_widget(page)
        if widget is None:
            log.warning(
                "No Turnstile widget visible to click; falling back to passive wait"
            )
        else:
            _click_turnstile_checkbox(page, widget)

    # Final wait for the token (post-click or passive).
    remaining = max(1000, timeout - grace)
    try:
        page.wait_for_function(_TURNSTILE_SOLVED_JS, timeout=remaining)
        log.info("Turnstile resolved")
        return True
    except BrowserError:
        log.warning("Turnstile did not resolve within %dms total", timeout)
        return False


# reCAPTCHA v2 detection. The widget mounts two iframes inside its host
# page: a visible "anchor" iframe with the checkbox, and an off-screen
# "bframe" iframe that pops in for image challenges. The
# ``g-recaptcha-response`` textarea (initially empty) holds the
# resolution token once the challenge passes — its ``.value`` is the
# canonical "solved" signal Google's JS writes for the host site to
# pick up.
_RECAPTCHA_DETECT_JS = """
(() => {
  if (document.querySelector('iframe[src*="google.com/recaptcha"]')) return true;
  if (document.querySelector('textarea[name="g-recaptcha-response"]')) return true;
  if (typeof window.grecaptcha !== 'undefined' &&
      document.querySelector('.g-recaptcha, [data-sitekey]')) return true;
  return false;
})()
""".strip()

_RECAPTCHA_SOLVED_JS = """
(() => {
  const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
  return !!(ta && ta.value && ta.value.length > 0);
})()
""".strip()


def _click_recaptcha_checkbox(page: Page) -> bool:
    """Click the reCAPTCHA v2 anchor-iframe checkbox.

    Targets the anchor iframe via Playwright's ``frame_locator``
    (cross-origin iframes can be addressed by selector even though
    JS evaluation can't reach across the boundary), then clicks the
    ``#recaptcha-anchor`` div inside. The click sends a real
    mousedown/mouseup pair to the iframe content; Google's JS scores
    the fingerprint and either auto-passes the token (low risk) or
    escalates to the bframe image challenge (which we don't solve
    headless).

    Returns True if the click was issued, False if the iframe wasn't
    addressable.
    """
    try:
        anchor = page.frame_locator(
            'iframe[src*="google.com/recaptcha/api2/anchor"]'
        )
        checkbox = anchor.locator("#recaptcha-anchor")
        if checkbox.count() > 0:
            checkbox.click(timeout=5000)
            log.info("Clicked reCAPTCHA checkbox")
            return True
    except BrowserError as e:
        log.warning("[recaptcha] checkbox click failed: %s", e)
    return False


def wait_for_recaptcha(
    page: Page,
    timeout: int = 30000,
    auto_click_after_ms: Optional[int] = 5000,
) -> bool:
    """Wait for (and optionally actively solve) a reCAPTCHA v2 checkbox challenge.

    Parallel API to ``wait_for_turnstile`` but for Google reCAPTCHA.
    Detection probes for the anchor iframe + ``grecaptcha`` global +
    ``g-recaptcha-response`` textarea. If the textarea's value is
    populated when we look, returns True. Otherwise waits up to
    ``auto_click_after_ms`` for the invisible/scoring auto-pass; if
    that doesn't fire, clicks the anchor checkbox; then waits for the
    response token to populate within the overall ``timeout``.

    A False return almost always means the checkbox click escalated to
    the bframe image challenge — we don't ship an image solver, so
    the caller should fail the claim and retry the flow tomorrow (or
    surface the captcha to a human via ``--setup``).

    Pass ``auto_click_after_ms=None`` to disable the active click and
    behave like a passive waiter — useful in ``--setup`` mode where
    a human is driving and we don't want to race them.

    Args:
        page: Playwright page.
        timeout: Total ms to wait for resolution (covers both the
            auto-pass grace window and any post-click settle).
        auto_click_after_ms: Grace window for auto-pass before we
            click the checkbox ourselves. ``None`` disables the click.

    Returns:
        True if reCAPTCHA resolved (or wasn't present). False if it
        was present but didn't resolve in time.
    """
    detected = page.evaluate(_RECAPTCHA_DETECT_JS)
    if not detected:
        # Widgets are often injected asynchronously after a server
        # round-trip — give Google's script a chance to mount.
        try:
            page.wait_for_function(_RECAPTCHA_DETECT_JS, timeout=5000)
            detected = True
        except BrowserError:
            detected = False

    if not detected:
        log.debug("No reCAPTCHA detected on page")
        return True

    if page.evaluate(_RECAPTCHA_SOLVED_JS):
        log.debug("reCAPTCHA already solved")
        return True

    log.info("reCAPTCHA v2 detected")

    grace = auto_click_after_ms if auto_click_after_ms is not None else timeout
    grace = max(0, min(grace, timeout))
    if grace > 0:
        try:
            log.info(
                "Waiting up to %dms for reCAPTCHA invisible/scoring pass...",
                grace,
            )
            page.wait_for_function(_RECAPTCHA_SOLVED_JS, timeout=grace)
            log.info("reCAPTCHA auto-passed")
            return True
        except BrowserError:
            pass

    if auto_click_after_ms is None:
        log.info("Auto-click disabled; waiting passively for token...")
    else:
        if not _click_recaptcha_checkbox(page):
            log.warning(
                "Couldn't click reCAPTCHA checkbox; falling back to passive wait"
            )

    remaining = max(1000, timeout - grace)
    try:
        page.wait_for_function(_RECAPTCHA_SOLVED_JS, timeout=remaining)
        log.info("reCAPTCHA resolved")
        return True
    except BrowserError:
        log.warning(
            "reCAPTCHA did not resolve within %dms total — likely "
            "escalated to image challenge (not supported headless).",
            timeout,
        )
        return False


def google_oauth_login_page_make(
    button_selectors: Optional[Tuple[str, ...]] = None,
) -> Tuple[Callable[[Page], None], Callable[[], bool]]:
    """Create a Google OAuth login page action.

    The returned action clicks a "Sign in with Google" button on the current
    page, waits for the redirect to ``accounts.google.com``, and fills in
    ``GOOGLE_EMAIL`` / ``GOOGLE_PASSWORD`` from the environment. If no
    redirect happens (the Google session is already established via
    ``user_data_dir``), the action returns early and the OAuth flow
    completes silently.

    Args:
        button_selectors: Optional per-site override for the Google
            sign-in button candidates. When ``None`` (the default), the
            framework's ``_GENERIC_GOOGLE_OAUTH_BUTTON`` list is used.
            Override when a site's login page exposes multiple SSO
            buttons (Google, Facebook, Apple, etc.) and the generic
            class-based candidates would match the wrong one — e.g.
            Pulsz, where ``button.sso-button`` lands on Facebook
            because it appears before Google in DOM order.

    Returns:
        tuple[Callable[[Page], None], Callable[[], bool]]: A tuple of
            (page action, always-False ``was_claim_attempted``).
    """

    def _drive_google_popup(popup: Page) -> None:
        """Walk an OAuth popup through the account chooser and any consent.

        Assumes a pre-established Google session (from ``--setup``) — so the
        popup typically shows the account chooser followed by an optional
        consent/continue screen, then self-closes. We poll because the
        transition between screens is async and Google's exact markup
        varies across account states.

        Falls back to email/password if ``GOOGLE_EMAIL`` / ``GOOGLE_PASSWORD``
        are set and the popup demands credentials (rare once the profile is
        warm).
        """
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=10000)
        except BrowserError:
            pass

        clicked_account = False
        clicked_confirm = False
        # Poll for up to ~30s (60 * 500ms). The popup closing terminates early.
        for _ in range(60):
            if popup.is_closed():
                log.info("Google OAuth popup closed")
                return

            # 1. Account chooser — `data-identifier` carries the account's
            # email and is the stable hook across Google's account-picker
            # revisions. Click the first visible account once.
            if not clicked_account:
                try:
                    account = popup.locator("[data-identifier]").first
                    if account.count() > 0 and account.is_visible():
                        account.click(
                            delay=gaussian_random_delay(), timeout=5000
                        )
                        log.info("Clicked first Google account in chooser")
                        clicked_account = True
                        popup.wait_for_timeout(1500)
                        continue
                except BrowserError:
                    pass

            # 2. Consent / continue screen. Google uses different verbs
            # depending on scope/state; try the common ones.
            if not clicked_confirm:
                for text in ("Continue", "Allow", "Confirm", "Yes"):
                    try:
                        btn = popup.locator(f'button:has-text("{text}")').first
                        if btn.count() > 0 and btn.is_visible():
                            btn.click(
                                delay=gaussian_random_delay(), timeout=5000
                            )
                            log.info("Clicked '%s' on OAuth consent screen", text)
                            clicked_confirm = True
                            popup.wait_for_timeout(1500)
                            break
                    except BrowserError:
                        continue
                if clicked_confirm:
                    continue

            # 3. Fallback: email/password prompt (only if the saved session
            # went stale and credentials are provided).
            email = os.getenv("GOOGLE_EMAIL")
            if email and not clicked_account:
                try:
                    email_input = popup.locator('input[type="email"]').first
                    if email_input.count() > 0 and email_input.is_visible():
                        email_input.fill(email, timeout=3000)
                        popup.locator('button:has-text("Next")').first.click(
                            delay=gaussian_random_delay(), timeout=3000
                        )
                        log.info("Entered Google email in popup")
                        popup.wait_for_timeout(1500)
                        continue
                except BrowserError:
                    pass
            password = os.getenv("GOOGLE_PASSWORD")
            if password:
                try:
                    pw_input = popup.locator('input[type="password"]').first
                    if pw_input.count() > 0 and pw_input.is_visible():
                        pw_input.fill(password, timeout=3000)
                        popup.locator('button:has-text("Next")').first.click(
                            delay=gaussian_random_delay(), timeout=3000
                        )
                        log.info("Entered Google password in popup")
                        popup.wait_for_timeout(1500)
                        continue
                except BrowserError:
                    pass

            popup.wait_for_timeout(500)

        log.warning("Google OAuth popup still open after polling window")

    def google_login_page(page: Page):
        """Perform Google OAuth login on the given page.

        Handles two flows:
          1. **Popup** (new tab/window opens for the OAuth): common on sites
             that use a JS ``window.open()`` for the SSO button. The popup
             is driven via the account chooser + consent screens; the main
             page finishes its callback when the popup closes.
          2. **Same-tab redirect**: the main page navigates to
             ``accounts.google.com`` and back. Legacy fallback — uses
             ``GOOGLE_EMAIL`` / ``GOOGLE_PASSWORD`` from env if the session
             isn't already established.
        """
        page.wait_for_load_state("domcontentloaded", timeout=5000)

        # Some login pages (e.g. Zula) gate the Google button behind
        # Cloudflare Turnstile — even if the button isn't HTML-disabled,
        # clicking before the widget goes green triggers a hard reject.
        wait_for_turnstile(page, timeout=30000)

        google_button_selectors = button_selectors or _GENERIC_GOOGLE_OAUTH_BUTTON

        # Watch for a popup opened by the click. ``expect_page`` races the
        # click against a new-page event in the same browser context.
        #
        # ``no_wait_after=True`` is critical here: the SSO button often
        # kicks off a server-side handshake alongside the window.open, and
        # Playwright's default post-click nav-wait creates a driver-side
        # promise that orphans if the handshake takes longer than the
        # click timeout. The orphan crashes Node via its unhandled-
        # rejection handler before Python can catch the exception. Opting
        # out of the nav wait keeps the promise off the event loop; the
        # popup event we care about is observed by ``expect_page`` anyway.
        popup_page: Optional[Page] = None
        try:
            with page.context.expect_page(timeout=15000) as popup_info:
                clicked = False
                for selector in google_button_selectors:
                    # ``count()`` is synchronous and doesn't wait — skip
                    # non-matching selectors instantly. Without this the
                    # 10s click-timeout fires once per miss, so a list of
                    # 9 site-specific selectors could burn 80+ seconds
                    # before reaching the one that matches.
                    locator = page.locator(selector).first
                    if locator.count() == 0:
                        continue
                    try:
                        locator.click(
                            delay=gaussian_random_delay(),
                            timeout=10000,
                            no_wait_after=True,
                        )
                        log.info(
                            "Clicked Google sign-in button with selector: %s",
                            selector,
                        )
                        clicked = True
                        break
                    except BrowserError:
                        continue
                if not clicked:
                    raise PlaywrightError("Could not find Google sign-in button")
            popup_page = popup_info.value
        except BrowserError:
            # No popup opened — either the click failed, or the site uses a
            # same-tab redirect instead of a popup.
            popup_page = None

        if popup_page is not None:
            log.info("OAuth popup detected; driving consent in new window")
            _drive_google_popup(popup_page)
            # Main page should now be navigating back to the site. Wait
            # for it to leave any intermediate /AuthCallback URL.
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
                log.info("Google OAuth login completed successfully (popup)")
            except BrowserError as e:
                log.warning("Timeout waiting for post-OAuth load: %s", e)
            return

        # ---- Same-tab redirect fallback (legacy flow) ----
        try:
            page.wait_for_url("**/accounts.google.com/**", timeout=5000)
        except BrowserError:
            log.info(
                "No OAuth popup and no redirect to Google — session may "
                "already be established"
            )
            return

        email = os.getenv("GOOGLE_EMAIL")
        if not email:
            log.error("GOOGLE_EMAIL environment variable not set")
            return

        try:
            page.fill('input[type="email"]', email, timeout=5000)
            page.click(
                'button:has-text("Next")', delay=gaussian_random_delay(), timeout=3000
            )
            log.info("Entered Google email")
        except Exception as e:
            log.error("Failed to enter email: %s", e)
            return

        password = os.getenv("GOOGLE_PASSWORD")
        if not password:
            log.error("GOOGLE_PASSWORD environment variable not set")
            return

        try:
            page.wait_for_selector(
                'input[type="password"]', state="visible", timeout=10000
            )
            page.fill('input[type="password"]', password, timeout=5000)
            page.click(
                'button:has-text("Next")', delay=gaussian_random_delay(), timeout=3000
            )
            log.info("Entered Google password")
        except Exception as e:
            log.error("Failed to enter password: %s", e)
            return

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
            log.info("Google OAuth login completed successfully (same-tab)")
        except Exception as e:
            log.warning("Timeout waiting for redirect, continuing: %s", e)

    def was_claim_attempted() -> bool:
        return False

    return google_login_page, was_claim_attempted


def make_generic_accept_or_close_modals(
    main_enabled_selector: str, modal_selector: str, close_modal_selector: str
) -> Callable[[Page], bool]:
    """Claim the daily bonus on LuckyBird.io.

    Args:
        main_enabled_selector: The main CSS selector for enabled buttons on this site.
        modal_selector: The CSS selector for the modal dialog.
        close_modal_selector: The CSS selector for the close button on the modal.

    Returns:
        Callable[[Page], bool]: A function that accepts or closes modals on the given page.
    """

    def accept_or_close_modals(page: Page) -> bool:
        claimed: int = 0
        accept_tokens: set = set(
            ["accept", "claim", "get", "collect", "yes", "agree", "okay"]
        )

        try:
            log.info("Waiting for daily bonus modal to appear...")
            locator: Locator = page.locator(modal_selector)
            expect(locator.first).to_be_visible(timeout=5000)
        except AssertionError as _:
            log.info(
                "Initial alerts popups timed out, this likely means the daily bonus has already been claimed."
            )
            return False

        try:
            # Find the claim buttons that are not disabled
            enabled_buttons: Locator = page.locator(main_enabled_selector)
            # close_buttons: Locator = page.locator(close_modal_selector)

            for attempt in range(
                1, 11
            ):  # Cleaner: explicit range instead of manual counter
                if enabled_buttons.count() == 0:
                    break

                if attempt == 10:
                    log.warning(
                        "Exceeded maximum attempts to close all modals, aborting..."
                    )
                    break

                # Get the last enabled button, it is likely on top
                n = (
                    attempt
                    if enabled_buttons.count() >= attempt
                    else enabled_buttons.count()
                ) - 1
                button: Locator = enabled_buttons.nth(n)
                button_text: str = button.text_content()
                button_words: set = set(button_text.lower().split())

                log.info("Found enabled button with text: %s", button_text)
                if accept_tokens & button_words:
                    log.info(
                        "Found enabled button, clicking button with text: %s",
                        button_text,
                    )
                    highlight_element_handle(button.element_handle())

                    # Try normal click first, then force if it fails
                    try:
                        button.click(delay=gaussian_random_delay(), timeout=5000)
                        # enabled_buttons = page.locator(main_enabled_selector)
                        claimed += 1
                    except BrowserError as e:
                        log.warning(
                            "clicking button failed... trying on next iterator: %s",
                            str(e),
                        )
                        # Check if there's a blocking modal on top and try to close it
                        # if close_buttons.count() > 0:
                        #     log.info("Attempting to close potentially blocking modal...")
                        #     try:
                        #         # Try closing the topmost modal (last in DOM order is typically on top)
                        #         close_button: Locator = close_buttons.last
                        #         close_button.click(delay=gaussian_random_delay(), timeout=3000)
                        #         wait_for_load_all_safe(page, timeout=1000)
                        #     except BrowserError as close_err:
                        #         log.debug("Could not close blocking modal: %s", str(close_err))
                else:
                    log.info(
                        "Button text does not contain any accept tokens, skipping..."
                    )
                    log.debug("Button text: %s", button_text)
                    highlight_element_handle(button.element_handle())
                    # Maybe try to close the modal instead
                    # if close_buttons.count() > 0:
                    #     log.info("Attempting to close modal instead...")
                    #     close_button: Locator = close_buttons.first
                    #     close_button.click(delay=gaussian_random_delay(), timeout=5000)

                wait_for_load_all_safe(page, timeout=3000)
                # Locator automatically re-queries the DOM, no need to reassign? Try anyway.
                # close_buttons = page.locator(close_modal_selector)

            log.info("Successfully processed all modals!")
        except BrowserError as e:
            log.error("Error clicking button for daily: %s", str(e))

        # Try to close the modal if it's still open
        try:
            close_button: Locator = page.locator(close_modal_selector)
            if close_button.count() > 0:
                close_button.first.click(delay=gaussian_random_delay(), timeout=3000)
                log.info("Closed daily bonus modal")
        except BrowserError:
            log.warning("Could not close daily bonus modal")

        return claimed

    return accept_or_close_modals


def make_simple_claim_button(
    btn_selector: str,
    pre_open_selector: Optional[str] = None,
    post_claim_close_selector: Optional[str] = None,
) -> Callable[[Page], bool]:
    """Make a page action that clicks a single on-page claim button.

    Args:
        btn_selector: Selector for the claim button.
        pre_open_selector: Optional selector for a notification / trigger
            element that opens the claim dialog (e.g. a daily-bonus toast
            or a header bonus button). Clicked only if the claim button
            isn't already on screen — so this is a no-op when the dialog
            auto-popped on page load and a real opener when it didn't.
            ``pre_open_selector`` may be a comma-separated list of CSS
            selectors; the first one that's visible wins.
        post_claim_close_selector: Optional selector for a confirmation
            modal's close button, clicked after a successful claim.

    Returns:
        A callable that returns True if the button was clicked, False if it
        wasn't present (already claimed today, or wrong page state) or the
        click failed. Never raises.
    """

    def simple_claim(page: Page) -> bool:
        # Post-OAuth lobby hydration can take 5–15s as the React state
        # subscribes to balance/bonus state. Wait for ANY of the pre-open
        # triggers OR the claim button to become visible before deciding
        # there's nothing to claim — otherwise an instant probe right
        # after login bails on a page that just hasn't rendered yet.
        candidate_selectors: List[str] = []
        if pre_open_selector:
            candidate_selectors.extend(
                s.strip() for s in pre_open_selector.split(",") if s.strip()
            )
        candidate_selectors.append(btn_selector)
        union_selector = ", ".join(candidate_selectors)
        try:
            page.locator(union_selector).first.wait_for(
                state="visible", timeout=15000
            )
        except (AssertionError,) + BrowserError:
            log.info(
                "No claim entry-points visible after 15s; daily bonus already claimed or page not hydrated"
            )
            return False

        # If a trigger selector is configured and the claim button isn't
        # already visible (auto-pop didn't fire, or the user dismissed it
        # earlier in the session), click the trigger to open the dialog.
        if pre_open_selector:
            try:
                already_open = False
                pre_check = page.locator(btn_selector).first
                if pre_check.count() > 0 and pre_check.is_visible():
                    already_open = True
                if not already_open:
                    for sel in (s.strip() for s in pre_open_selector.split(",")):
                        if not sel:
                            continue
                        opener = page.locator(sel).first
                        if opener.count() > 0 and opener.is_visible():
                            log.info("Clicking pre-open trigger: %s", sel)
                            opener.click(
                                delay=gaussian_random_delay(), timeout=5000
                            )
                            wait_for_load_all_safe(page, timeout=3000)
                            break
                    else:
                        log.debug(
                            "No pre-open trigger visible; falling through to btn"
                        )
            except BrowserError as e:
                log.debug("Pre-open click skipped: %s", e)

        try:
            btn: Locator = page.locator(btn_selector).first
            expect(btn).to_be_visible(timeout=5000)
        except (AssertionError,) + BrowserError:
            log.info("Claim button not visible; daily bonus likely already claimed")
            return False

        # Disabled-check: COLLECT becomes ``disabled`` once today's bonus
        # is already claimed (Sportzino) — same pattern MTB uses. Without
        # this, ``btn.click()`` waits the full 5s for the element to
        # become enabled and only then times out.
        try:
            if btn.is_disabled():
                log.info("Claim button disabled; daily bonus already claimed today")
                return False
        except BrowserError:
            pass

        try:
            btn.click(delay=gaussian_random_delay(), timeout=5000)
            log.info("Clicked claim button")
        except BrowserError as e:
            log.warning("Claim button click failed: %s", str(e))
            return False

        wait_for_load_all_safe(page, timeout=3000)

        if post_claim_close_selector:
            try:
                close_btn = page.locator(post_claim_close_selector).first
                if close_btn.count() > 0 and close_btn.is_visible():
                    close_btn.click(delay=gaussian_random_delay(), timeout=3000)
            except BrowserError:
                pass

        return True

    return simple_claim


def url_to_env_prefix(url: str) -> str:
    """Convert a URL to an environment variable prefix.

    Args:
        url (str): The URL to convert.

    Returns:
        str: The environment variable prefix.
    """
    parsed_url = urlparse(url)
    netloc = parsed_url.netloc

    prefix = netloc.split(".")[0]
    return prefix.upper()


_env_loaded = False


def load_env_file(
    path: Optional[Union[str, Path]] = None, override: bool = False
) -> Optional[Path]:
    """Load ``KEY=VALUE`` pairs from a ``.env``-style file into ``os.environ``.

    When ``path`` is ``None``, looks for ``.env`` in the current working
    directory, then falls back to the legacy ``picks.env`` name with a
    deprecation warning. Returns the ``Path`` that was loaded, or ``None`` if
    no file was found.

    Supported syntax: blank lines, ``#`` comments, optional ``export `` prefix,
    and optional single- or double-quoted values. Variable expansion is not
    supported. Existing environment variables are preserved unless
    ``override=True``.
    """
    global _env_loaded

    if path is None:
        for candidate in (".env", "picks.env"):
            p = Path(candidate)
            if p.exists():
                if candidate == "picks.env":
                    log.warning(
                        "Loading credentials from picks.env — rename to .env; "
                        "the picks.env fallback is deprecated."
                    )
                path = p
                break
        else:
            _env_loaded = True
            return None
    else:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and (override or key not in os.environ):
                os.environ[key] = value

    _env_loaded = True
    log.info("Loaded environment from %s", path)
    return path


def _ensure_env_loaded() -> None:
    """Load ``.env`` once, lazily, on first credential lookup."""
    if not _env_loaded:
        load_env_file()


def get_credentials(url: str, twofa: bool = False) -> Tuple[str, str, str]:
    """Get the credentials for Tronpick.

    Raises:
        ValueError: If the credentials are not set.

    Returns:
        tuple[str, str]: The username and password.
    """
    _ensure_env_loaded()

    env_prefix = url_to_env_prefix(url)

    username = os.getenv(f"{env_prefix}_USERNAME")
    password = os.getenv(f"{env_prefix}_PASSWORD")
    if not username or not password:
        raise ValueError(f"{env_prefix}_USERNAME and {env_prefix}_PASSWORD must be set")

    totp_secret = os.getenv(f"{env_prefix}_2FA")
    if twofa and not totp_secret:
        raise ValueError(f"{env_prefix}_2FA must be set when twofa=True")

    return username, password, totp_secret


def gaussian_random_delay(mean: float = 50, stddev: float = 10) -> int:
    """Generate a Gaussian random delay in milliseconds.
    The defaults are chosen to (hopefully) simulate human-like delays.

    Args:
        mean (float, optional): The mean delay in milliseconds. Defaults to 50.
        stddev (float, optional): The standard deviation of the delay in milliseconds. Defaults to 10.

    Returns:
        int: A random delay in milliseconds.
    """
    return int(max(0, random.gauss(mean, stddev)))


def wait_for_clickable(
    page: Page,
    selector: str,
    timeout: int = CLICK_TIMEOUT_MS,
    scroll_into_view: bool = True,
) -> bool:
    """Wait for an element to be clickable (visible and enabled).

    Args:
        page (Page): The Playwright page object.
        selector (str): The CSS selector for the element.
        timeout (int, optional): Maximum wait time in milliseconds. Defaults to CLICK_TIMEOUT_MS.
        scroll_into_view (bool, optional): Whether to scroll element into view. Defaults to True.

    Returns:
        bool: True if element is clickable, False otherwise.
    """
    try:
        locator = page.locator(selector)

        # Wait for element to be visible
        locator.wait_for(state="visible", timeout=timeout)

        # Scroll into view if requested
        if scroll_into_view:
            try:
                locator.scroll_into_view_if_needed(timeout=timeout // 2)
            except Exception as e:
                log.debug("Could not scroll element into view: %s", e)

        # Check if element is enabled (not disabled)
        if locator.is_disabled():
            log.warning("Element %s is disabled", selector)
            return False

        return True
    except Exception as e:
        log.warning("Element %s not clickable within timeout: %s", selector, e)
        return False


def check_click_intercept(page: Page, selector: str) -> Optional[Dict]:
    """Detect whether something is on top of ``selector``'s click point.

    Uses ``document.elementFromPoint`` at the target's bounding-box
    center to identify what would actually receive a click. Returns
    None if the target (or one of its descendants) is on top — i.e.
    clickable. Otherwise returns a diagnostic dict so callers can
    log *what* blocked them instead of just retrying blind.

    The diagnostic is the same shape the manual probes we used during
    the SLNGApp-family debugging session produced: target rect, the
    element actually on top, and a 5-deep ancestor chain so a class
    like ``.dialog-container`` can be traced back to its semantic
    parent. Returns None on any error (selector missing, zero-size
    element, etc.) — caller's normal click-then-wait path handles
    those cases.

    Returns:
        ``None`` if not intercepted (clickable), else ``dict`` with
        keys ``target_selector``, ``target_rect`` (x/y/w/h/cx/cy),
        ``intercepted_by`` (tag/className/id/position/zIndex), and
        ``ancestor_chain`` (list of up to 5 ancestors).
    """
    try:
        info = page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return null;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return null;
                const cx = r.x + r.width / 2;
                const cy = r.y + r.height / 2;
                const top = document.elementFromPoint(cx, cy);
                if (!top) return null;
                if (top === el || el.contains(top)) return null;
                const ancestors = [];
                let n = top;
                while (n && ancestors.length < 5) {
                    const cs = getComputedStyle(n);
                    ancestors.push({
                        tag: n.tagName,
                        className: (n.className || '').toString().slice(0, 200),
                        id: n.id,
                        position: cs.position,
                        zIndex: cs.zIndex,
                    });
                    n = n.parentElement;
                }
                return {
                    target_rect: {x: r.x, y: r.y, w: r.width, h: r.height, cx, cy},
                    intercepted_by: ancestors[0],
                    ancestor_chain: ancestors,
                };
            }""",
            selector,
        )
    except BrowserError as e:
        log.debug("check_click_intercept(%s) failed: %s", selector, e)
        return None
    if info is None:
        return None
    info["target_selector"] = selector
    return info


def safe_click(
    page: Page,
    selector: str,
    timeout: int = CLICK_TIMEOUT_MS,
    max_retries: int = MAX_CLICK_RETRIES,
    delay: Optional[int] = None,
    force: bool = False,
    scroll_into_view: bool = True,
    detect_intercept: bool = True,
    pause_on_stuck: bool = PAUSE_ON_STUCK,
) -> bool:
    """Perform a click operation with timeout, retry, and stuck-detection.

    Args:
        page (Page): The Playwright page object.
        selector (str): The CSS selector for the element to click.
        timeout (int, optional): Maximum wait time per attempt in milliseconds. Defaults to CLICK_TIMEOUT_MS.
        max_retries (int, optional): Maximum number of retry attempts. Defaults to MAX_CLICK_RETRIES.
        delay (int, optional): Click delay in milliseconds. If None, uses gaussian_random_delay().
        force (bool, optional): Whether to force the click. Defaults to False.
            Implicitly disables ``detect_intercept`` (force-click bypasses
            actionability checks anyway).
        scroll_into_view (bool, optional): Whether to scroll element into view first. Defaults to True.
        detect_intercept (bool, optional): Run ``check_click_intercept``
            before each attempt. On detected intercept, log a structured
            warning naming the blocker (its tag, className, z-index, and
            ancestor chain) and return False without burning the full
            Playwright actionability-retry budget. Default True; pass
            False for the rare case where a transient intercept is
            expected and you'd rather let Playwright retry through it.
        pause_on_stuck (bool, optional): On detected intercept, call
            ``page.pause()`` to drop into Playwright's Inspector for
            interactive debugging. Defaults to env-var-driven
            ``PAUSE_ON_STUCK`` (controlled by ``--pause-on-stuck``
            on runner.py). Inspector needs a display — don't pair
            with ``--headless``.

    Returns:
        bool: True if click succeeded, False otherwise (intercepted,
        not clickable, or all retries exhausted).
    """
    if delay is None:
        delay = gaussian_random_delay()

    # Once-only fast-fail prechecks — categorize each failure with an
    # actionable ``[click_failed:%s] reason=...`` line so callers (and
    # log readers) can tell intercept/not-found/ambiguous/disabled apart
    # without parsing Playwright's stack-trace prose. Retry budget is
    # reserved for transient cases (visibility / stability races).
    if not force:
        # 1. Intercept — something else on top of the click point.
        if detect_intercept:
            intercept = check_click_intercept(page, selector)
            if intercept:
                blocker = intercept["intercepted_by"]
                log.warning(
                    "[stuck] %s click intercepted by <%s class=%r id=%r "
                    "position=%s z-index=%s>; rect=%s; ancestor chain=%s",
                    selector,
                    blocker["tag"],
                    blocker["className"],
                    blocker["id"],
                    blocker["position"],
                    blocker["zIndex"],
                    intercept["target_rect"],
                    intercept["ancestor_chain"],
                )
                if pause_on_stuck:
                    log.info(
                        "[stuck] PAUSE_ON_STUCK set; opening Playwright "
                        "Inspector. Click 'Resume' in the inspector to "
                        "continue (or close it to abort)."
                    )
                    try:
                        page.pause()
                    except BrowserError as e:
                        log.warning(
                            "[stuck] page.pause() failed (no display? "
                            "running headless?): %s",
                            e,
                        )
                log.error(
                    "[click_failed:%s] reason=intercepted "
                    "blocker=<%s class=%r>",
                    selector,
                    blocker["tag"],
                    blocker["className"],
                )
                return False

        # 2. Selector validity — wait briefly for the element to attach
        # (covers modal-open animations and lazy-rendered components),
        # then check for true not-found vs ambiguous-multi-match.
        # The 2s grace replaces an immediate ``count()`` check that was
        # too aggressive when ``safe_click`` is called right after a
        # click that opens a modal — the tab/button inside often takes
        # 100-500ms to mount and the immediate count returned 0.
        # Note: ``wait_for_selector`` returns immediately when the
        # element is already present, so no cost on the common case.
        try:
            page.wait_for_selector(selector, state="attached", timeout=2000)
        except BrowserError:
            log.error(
                "[click_failed:%s] reason=not_found "
                "(selector matched zero elements within 2s grace window)",
                selector,
            )
            return False
        try:
            n_matches = page.locator(selector).count()
        except BrowserError:
            n_matches = 1  # fall through to retry loop on ambiguous error
        if n_matches > 1:
            log.error(
                "[click_failed:%s] reason=ambiguous_selector matches=%d "
                "(Playwright strict mode rejects multi-match — narrow the "
                "selector or append .first / :nth-of-type(N))",
                selector,
                n_matches,
            )
            return False

        # 3. Disabled state — element exists and is alone, but is
        # explicitly disabled (e.g. claim-once button after the claim).
        # Skip if the caller already disambiguated via their own
        # is_disabled() (idempotent — just logs and bails fast here).
        try:
            if page.locator(selector).is_disabled():
                log.warning(
                    "[click_failed:%s] reason=disabled "
                    "(element exists but ``disabled`` attribute is set)",
                    selector,
                )
                return False
        except BrowserError:
            # is_disabled can raise on detached / animating elements;
            # let the retry loop's own checks handle that case.
            pass

    # Retry loop — handles transient visibility / stability races and
    # Playwright click-time exceptions. The hopeless cases above already
    # short-circuited.
    for attempt in range(max_retries):
        try:
            if not force and not wait_for_clickable(
                page, selector, timeout, scroll_into_view
            ):
                log.warning(
                    "Element %s not clickable on attempt %d/%d",
                    selector,
                    attempt + 1,
                    max_retries,
                )
                if attempt < max_retries - 1:
                    # Exponential backoff
                    backoff_time = 1000 * (2**attempt)
                    log.info("Waiting %dms before retry", backoff_time)
                    page.wait_for_timeout(backoff_time)
                    continue
                else:
                    log.error(
                        "[click_failed:%s] reason=not_clickable_after_%d_attempts",
                        selector,
                        max_retries,
                    )
                    return False

            # Perform the click
            page.click(selector, delay=delay, timeout=timeout, force=force)
            log.debug("Successfully clicked %s on attempt %d", selector, attempt + 1)
            return True

        except Exception as e:
            err_msg = str(e)[:200]
            log.warning(
                "Click failed on attempt %d/%d for %s: %s",
                attempt + 1,
                max_retries,
                selector,
                err_msg,
            )

            if attempt < max_retries - 1:
                # Exponential backoff
                backoff_time = 1000 * (2**attempt)
                log.info("Waiting %dms before retry", backoff_time)
                page.wait_for_timeout(backoff_time)
            else:
                log.error(
                    "[click_failed:%s] reason=exception details=%s",
                    selector,
                    err_msg,
                )
                return False

    return False


def get_arg_parser(description: str = "Generic Daily Bonus Claimer") -> ArgumentParser:
    """Get argument parser for command-line options.
    Args:
        description (str): Description for the argument parser
    Returns:
        ArgumentParser: Configured argument parser
    """
    parser = ArgumentParser(description=description)
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy server to use (e.g., http://user:pass@host:port)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no GUI)",
    )
    parser.add_argument(
        "--google-oauth",
        action="store_true",
        help="Enable Google OAuth handling (if applicable)",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help=(
            "Interactive setup mode: launch non-headless browser, run "
            "pre-login to reach the login page, then pause so you can "
            "complete OAuth (or any other first-time auth) by hand. "
            "Persists the session into --user-data-dir and exits."
        ),
    )
    parser.add_argument(
        "--skip-claim",
        action="store_true",
        help="Skip claiming the daily bonus",
    )
    parser.add_argument(
        "--user-data-dir",
        type=str,
        default=None,
        help="Path to user data directory for browser session",
    )
    return parser


# ============================================================================
# Parameterized Casino Configuration System
# ============================================================================


@dataclass
class LoginConfig:
    """Configuration for login form selectors and behavior."""

    username_selector: str
    password_selector: str
    login_submit_selector: str
    totp_code_selector: Optional[str] = None
    totp_submit_selector: Optional[str] = None
    # Sugar over ``pre_login_callback`` for the common case of "click a
    # header login button to navigate from the homepage to the actual
    # /login page". Set this when ``login_url`` points at the site root
    # and the OAuth PKCE challenge is minted client-side by the header
    # button's onClick (Sportzino, Zula). Ignored if
    # ``pre_login_callback`` is also set — the callback wins.
    pre_login_click_selector: Optional[str] = None
    pre_login_callback: Optional[Callable[[Page], None]] = None
    post_login_callback: Optional[Callable[[Page], None]] = None
    # Per-site override for the Google "Sign in with Google" button
    # candidates. ``None`` falls back to the generic
    # ``_GENERIC_GOOGLE_OAUTH_BUTTON`` list. Set this when a site's
    # login page has multiple SSO buttons and the generic class-based
    # candidates (e.g. ``button.sso-button``) would land on the wrong
    # one — Pulsz being the canonical example.
    google_oauth_btn_selectors: Optional[Tuple[str, ...]] = None


@dataclass
class MTBClaimConfig:
    """Configuration for Modal-Tab-Button claiming pattern."""

    modal_selector: str  # Button to open modal (e.g., wallet button)
    btn_selector: str  # Claim button
    close_btn_selector: str  # Modal close button
    # Tab inside modal (e.g., daily bonus tab). ``None`` for sites
    # whose claim modal opens directly to the daily-bonus view (e.g.
    # YayCasino's coin-store modal has no tab switcher).
    tab_selector: Optional[str] = None
    # Wait budget for ``btn_selector`` to become clickable after the
    # modal-open click. Default covers UI animation slack; bump for
    # flows where the CTA only appears after a multi-second animation
    # (PulszBingo's wheel spins ~6-8s before "GET MY COINS" renders).
    btn_visibility_timeout_ms: int = 5000
    # Per-site marker for already-claimed state. Checked AFTER the
    # modal+tab clicks but BEFORE waiting for ``btn_selector`` —
    # if any matching element is visible, log "Daily bonus already
    # claimed." and exit cleanly. Required for sites whose
    # already-claimed surface differs from both the framework's
    # built-in detection paths:
    #   1. ``modal_selector`` not visible (Modo, PulszBingo) — daily
    #      trigger button literally disappears post-claim.
    #   2. ``btn_selector`` is_disabled() (yay, americanluck, stake) —
    #      Claim button stays mounted but ``disabled`` attribute set.
    # Shuffle.us is a third pattern: Claim button is REMOVED from DOM
    # entirely, replaced by a "TimeRemain" countdown
    # ("5h 58m 32s until claim") — set this to
    # ``'p[class*="TimeRemain_timeRemain"]'`` to detect it cleanly
    # instead of triggering a [click_failed:claim_button_not_found]
    # every day after the first claim.
    already_claimed_selector: Optional[str] = None


@dataclass
class GenericClaimConfig:
    """Configuration for Generic Accept/Close modal claiming pattern."""

    main_enabled_selector: str  # Selector for enabled action buttons
    modal_selector: str  # Modal container selector
    close_modal_selector: str  # Close button selector


@dataclass
class SimpleClaimConfig:
    """Configuration for a single-button claim pattern.

    For sites where claiming is just "find and click one button on the page",
    with no wallet modal to open first and no cascade of other modals to
    dismiss afterward — e.g. SpinQuest's homepage "claim now" CTA.

    The optional ``pre_open_selector`` extends this pattern to sites that
    show a notification / trigger first (Sportzino: a ``daily-bonus-
    notification`` toast OR an animated bottom-nav STORE button opens the
    ``daily-bonus-dialog`` whose ``proceed-button`` is the actual claim).
    The trigger only fires when the claim button isn't already visible —
    so it's a no-op when the dialog auto-popped on page load.
    """

    btn_selector: str  # Claim button selector
    # Optional opener clicked when btn_selector isn't already visible.
    # CSS selector; comma-separated lets you list fallbacks (e.g. toast
    # first, then store button) — the first visible one wins.
    pre_open_selector: Optional[str] = None
    post_claim_close_selector: Optional[str] = None  # Optional confirmation-modal close


@dataclass
class CasinoConfig:
    """Complete configuration for a casino automation script.

    This dataclass contains all parameters needed to create a fully functional
    casino automation script using the factory function.
    """

    # Required: Basic information
    name: str  # Casino name (e.g., "Stake.us")
    url: str  # Main casino URL
    login_url: str  # Login page URL (can be same as url)

    # Required: Login configuration
    login: LoginConfig

    # Required: Currency display configuration
    currency_display: CurrencyDisplayConfig

    # Bonus claiming configuration (choose one pattern). Optional only
    # when ``custom_claim_action`` is provided below; one of the two
    # must be set or ``make_casino_automation`` raises at construction
    # time.
    claim_config: Optional[MTBClaimConfig | GenericClaimConfig | SimpleClaimConfig] = None
    claim_pattern: Literal["mtb", "generic", "simple"] = "mtb"

    # Optional: Custom claim action. Use this for sites whose claim
    # flow doesn't fit any of the built-in MTB / Simple / Generic
    # patterns — e.g. McLuck, where a Cloudflare Turnstile mounts
    # mid-claim and requires a solve + re-click of the claim button.
    # When set, the dispatch in ``make_casino_automation`` skips
    # ``claim_config`` entirely. The callable should emit the
    # canonical "Daily bonus claimed." / "Daily bonus already
    # claimed." log lines so the runner's outcome parser categorizes
    # the run correctly.
    custom_claim_action: Optional[Callable[[Page], None]] = None

    # Optional: Custom balance parser. Use this for sites whose
    # currency-toggle pattern doesn't fit the default
    # ``make_get_casino_account_state`` factory — e.g. SpinQuest,
    # which has a toggle button + react-toastify confirmation
    # ("You've switched to SweepsCoins/GoldCoins") rather than
    # a per-currency activator. Should return a populated
    # ``CasinoAccountState`` so the canonical "Account State: ..."
    # log line shape stays consistent for the runner's regex.
    custom_balance_parser: Optional[Callable[[Page], CasinoAccountState]] = None

    # Optional: Additional page actions to perform after standard flow
    additional_actions: Optional[List[Callable[[Page], None]]] = field(
        default_factory=list
    )

    # Optional: Custom page action timeout
    page_wait_timeout: int = 5000

    # Optional: Fetch timeout
    fetch_timeout: int = 60000

    # Optional: Description for CLI
    description: str = "Casino Automation Script"

    # Optional: Whether 2FA is required
    requires_2fa: bool = False

    # Optional: Enable Camoufox's IP-derived geolocation mode.
    # When True, Camoufox derives coords + timezone + locale from the
    # connection's real IP, keeping all fingerprint signals internally
    # consistent. Use for sites with regulatory-grade geo validation
    # (GeoComply, etc.) where the user is legitimately in an allowed
    # jurisdiction on their own IP. Leave False for adversarial
    # geo-spoofing (in which case use make_set_geolocation explicitly).
    geoip: bool = False

    # Enable Camoufox's built-in Cloudflare challenge auto-solver. Handles
    # Turnstile (including the inline widget variant) and IUAM "checking
    # your browser" interstitials. Use on sites where the invisible
    # Turnstile path doesn't consistently auto-pass in Camoufox — notably
    # Zula, where clicking the SSO button before the widget is green
    # triggers a hard reject. No effect on sites without Cloudflare
    # challenges, so it's also safe-to-enable broadly.
    solve_cloudflare: bool = False

    # Browser backend:
    #   "camoufox" (default) — StealthySession on a stealthed Firefox fork.
    #     Best for geoip-gated sites (GeoComply etc.) and has the most
    #     stealth options. Downside: Firefox-derived fingerprint is rarer
    #     than Chrome's, so Turnstile sometimes pushes to visible challenges.
    #   "chrome" — DynamicSession on Patchright's stealth Chromium (or real
    #     Chrome via ``real_chrome=True``). Chrome's fingerprint is the
    #     commonest on the web so Turnstile risk-scoring almost always runs
    #     the invisible-pass path. Does NOT support geoip or solve_cloudflare.
    #
    # Switching backends invalidates the persisted ``user_data_dir`` (Firefox
    # profile layout ≠ Chrome profile layout); rerun with ``--setup`` after
    # flipping this.
    browser_backend: Literal["camoufox", "chrome"] = "camoufox"

    # Only meaningful when ``browser_backend="chrome"``. When True, Scrapling
    # launches the system's installed Chrome binary (most realistic
    # fingerprint). When False, uses Patchright's bundled Chromium, which is
    # close enough for most Turnstile challenges and requires no extra setup.
    real_chrome: bool = False
