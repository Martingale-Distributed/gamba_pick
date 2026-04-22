from argparse import ArgumentParser
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Tuple, Dict, Literal
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
                        text = element_selector.first.text_content().strip()
                        # Remove currency symbols and parse
                        text = text.replace(currency.code, "").replace(",", "").strip()
                        try:
                            n = float(text)
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
        except PlaywrightError as _:
            # Sometimes networkidle doesn't happen, ignore.
            pass
    except PlaywrightError as e:
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
        except PlaywrightError as e:
            log.error("Exception occurred while claiming daily bonus: %s", str(e))
        finally:
            # Close the wallet modal if it's still open
            try:
                close_btn = page.locator(close_btn_selector)
                if close_btn.count() > 0:
                    close_btn.click(delay=gaussian_random_delay(), timeout=10000)
            except PlaywrightError:
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
    except PlaywrightError as e:
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
    except PlaywrightError as e:
        log.error("Error highlighting element: %s", str(e))


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
                    except PlaywrightError as e:
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
                        #     except PlaywrightError as close_err:
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
        except PlaywrightError as e:
            log.error("Error clicking button for daily: %s", str(e))

        # Try to close the modal if it's still open
        try:
            close_button: Locator = page.locator(close_modal_selector)
            if close_button.count() > 0:
                close_button.first.click(delay=gaussian_random_delay(), timeout=3000)
                log.info("Closed daily bonus modal")
        except PlaywrightError:
            log.warning("Could not close daily bonus modal")

        return claimed

    return accept_or_close_modals


def make_simple_claim_button(
    btn_selector: str,
    post_claim_close_selector: Optional[str] = None,
) -> Callable[[Page], bool]:
    """Make a page action that clicks a single on-page claim button.

    Args:
        btn_selector: Selector for the claim button.
        post_claim_close_selector: Optional selector for a confirmation
            modal's close button, clicked after a successful claim.

    Returns:
        A callable that returns True if the button was clicked, False if it
        wasn't present (already claimed today, or wrong page state) or the
        click failed. Never raises.
    """

    def simple_claim(page: Page) -> bool:
        try:
            btn: Locator = page.locator(btn_selector).first
            expect(btn).to_be_visible(timeout=5000)
        except (AssertionError, PlaywrightError):
            log.info("Claim button not visible; daily bonus likely already claimed")
            return False

        try:
            btn.click(delay=gaussian_random_delay(), timeout=5000)
            log.info("Clicked claim button")
        except PlaywrightError as e:
            log.warning("Claim button click failed: %s", str(e))
            return False

        wait_for_load_all_safe(page, timeout=3000)

        if post_claim_close_selector:
            try:
                close_btn = page.locator(post_claim_close_selector).first
                if close_btn.count() > 0 and close_btn.is_visible():
                    close_btn.click(delay=gaussian_random_delay(), timeout=3000)
            except PlaywrightError:
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


def get_credentials(url: str, twofa: bool = False) -> Tuple[str, str, str]:
    """Get the credentials for Tronpick.

    Raises:
        ValueError: If the credentials are not set.

    Returns:
        tuple[str, str]: The username and password.
    """

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
    """

    btn_selector: str  # Claim button selector
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
