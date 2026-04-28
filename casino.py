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

# Default contant values
CLICK_TIMEOUT_MS = 5000  # Default timeout for click operations
MAX_CLICK_RETRIES = 3  # Maximum number of retry attempts for failed clicks
HANG_DETECTION_SECONDS = 30  # Seconds without balance change before detecting hang
MAX_KENO_ITERATIONS = 1000  # Maximum iterations in gambling loop as safety net


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
    """Represents the current state of a Stake.us casino account."""

    sweeps_coins: float = 0.0  # SC balance
    gold_coins: float = 0.0  # GC balance
    vip_level: str = "None"  # VIP level (Bronze, Silver, Gold, Platinum, Diamond, etc.)
    vip_progress: Optional[float] = None  # Progress to next VIP level (0.0-1.0)

    def __str__(self) -> str:
        return f"SC: {self.sweeps_coins:.2f}, GC: {self.gold_coins:.2f}, VIP: {self.vip_level}"


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
    ):
        self.currencies = currencies
        self.currency_toggle_dropdown_selector = currency_toggle_dropdown_selector
        self.currency_toggle_switch_selector = currency_toggle_switch_selector


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
        # OAuth lobby hydration can take 5-15s as React subscribes to
        # balance state. Wait for ``attached`` rather than ``visible``:
        # count-up animation containers are sometimes styled with
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
                    state="attached", timeout=15000
                )
            except (AssertionError,) + BrowserError:
                log.warning(
                    "No currency selectors attached after 15s; balance read may be 0"
                )

        sweeps_coins = 0.0
        gold_coins = 0.0
        vip_level = "None"
        vip_progress = None

        # Try to parse Stake Cash balance
        for currency in currency_display_config.currencies:
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
                        log.debug(
                            "Currency %s selector %s: raw=%r cleaned=%r",
                            currency.code,
                            selector,
                            raw,
                            cleaned,
                        )
                        try:
                            n = float(cleaned)
                            log.info(f"Found {currency.name} balance: {n}")
                            if currency.code == "SC":
                                sweeps_coins = n
                            elif currency.code == "GC":
                                gold_coins = n
                            break
                        except ValueError:
                            continue
                except Exception as e:
                    log.debug(f"Selector {selector} failed for {currency.name}: {e}")
                    continue
            if currency_display_config.currency_toggle_switch_selector:
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
            sweeps_coins=sweeps_coins,
            gold_coins=gold_coins,
            vip_level=vip_level,
            vip_progress=vip_progress,
        )

    return get_casino_account_state


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

        # 1. Site-specific close button (fastest path when it works).
        if close_selector:
            close_btn = page.locator(close_selector).first
            try:
                if close_btn.count() > 0 and close_btn.is_visible():
                    close_btn.click(delay=gaussian_random_delay(), timeout=3000)
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
                    cand.click(delay=gaussian_random_delay(), timeout=3000)
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
                    fb.click(delay=gaussian_random_delay(), timeout=3000)
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
    # tab selector, usually daily bonus button
    tab_selector='button[data-testid="dailyBonus"]',
    # button selector, usually claim button
    btn_selector="button.justify-center:nth-child(4)",
    # close modal selector
    close_btn_selector='button[data-testid="modal-close"]',
) -> Callable[[Page], None]:
    """Generator for claiming daily bonus via modal, tab, button pattern.
    Args:
        modal_selector (str): Selector for the modal open button.
        tab_selector (str): Selector for the tab button inside the modal.
        btn_selector (str): Selector for the claim button.
        close_btn_selector (str): Selector for the modal close button.
    Returns:
        Callable[[Page], None]: A function that performs the daily bonus claim action on the given page.
    """
    log.debug(
        "selectors: %s, %s, %s, %s",
        modal_selector,
        tab_selector,
        btn_selector,
        close_btn_selector,
    )

    def claim_daily_bonus(page: Page) -> None:
        """Clicks to claim the daily bonus if available.
        Args:
            page (Page): The Playwright page object.

        Returns:
        """

        try:
            page.click(modal_selector, delay=gaussian_random_delay(), timeout=5000)
            page.click(tab_selector, delay=gaussian_random_delay(), timeout=5000)
            claim_btn = page.locator(btn_selector)
            if claim_btn.is_disabled():
                log.info("Daily bonus already claimed.")
            else:
                claim_btn.click(delay=gaussian_random_delay(), timeout=5000)
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


def google_oauth_login_page_make() -> Tuple[
    Callable[[Page], None], Callable[[], bool]
]:
    """Create a Google OAuth login page action.

    The returned action clicks a "Sign in with Google" button on the current
    page, waits for the redirect to ``accounts.google.com``, and fills in
    ``GOOGLE_EMAIL`` / ``GOOGLE_PASSWORD`` from the environment. If no
    redirect happens (the Google session is already established via
    ``user_data_dir``), the action returns early and the OAuth flow
    completes silently.

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

        google_button_selectors = _GENERIC_GOOGLE_OAUTH_BUTTON

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
            close_buttons: Locator = page.locator(close_modal_selector)

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


def safe_click(
    page: Page,
    selector: str,
    timeout: int = CLICK_TIMEOUT_MS,
    max_retries: int = MAX_CLICK_RETRIES,
    delay: Optional[int] = None,
    force: bool = False,
    scroll_into_view: bool = True,
) -> bool:
    """Perform a click operation with timeout and retry logic.

    Args:
        page (Page): The Playwright page object.
        selector (str): The CSS selector for the element to click.
        timeout (int, optional): Maximum wait time per attempt in milliseconds. Defaults to CLICK_TIMEOUT_MS.
        max_retries (int, optional): Maximum number of retry attempts. Defaults to MAX_CLICK_RETRIES.
        delay (int, optional): Click delay in milliseconds. If None, uses gaussian_random_delay().
        force (bool, optional): Whether to force the click. Defaults to False.
        scroll_into_view (bool, optional): Whether to scroll element into view first. Defaults to True.

    Returns:
        bool: True if click succeeded, False otherwise.
    """
    if delay is None:
        delay = gaussian_random_delay()

    for attempt in range(max_retries):
        try:
            # Wait for element to be clickable
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
                    return False

            # Perform the click
            page.click(selector, delay=delay, timeout=timeout, force=force)
            log.debug("Successfully clicked %s on attempt %d", selector, attempt + 1)
            return True

        except Exception as e:
            log.warning(
                "Click failed on attempt %d/%d for %s: %s",
                attempt + 1,
                max_retries,
                selector,
                str(e)[:100],
            )

            if attempt < max_retries - 1:
                # Exponential backoff
                backoff_time = 1000 * (2**attempt)
                log.info("Waiting %dms before retry", backoff_time)
                page.wait_for_timeout(backoff_time)
            else:
                log.error("All click attempts failed for %s", selector)
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


@dataclass
class MTBClaimConfig:
    """Configuration for Modal-Tab-Button claiming pattern."""

    modal_selector: str  # Button to open modal (e.g., wallet button)
    tab_selector: str  # Tab inside modal (e.g., daily bonus tab)
    btn_selector: str  # Claim button
    close_btn_selector: str  # Modal close button


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

    # Required: Bonus claiming configuration (choose one pattern)
    claim_config: MTBClaimConfig | GenericClaimConfig | SimpleClaimConfig
    claim_pattern: Literal["mtb", "generic", "simple"] = "mtb"

    # Optional: Custom balance parser
    custom_balance_parser: Optional[Callable[[Page], Dict[str, Optional[float]]]] = None

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
