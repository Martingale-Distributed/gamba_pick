import contextlib
import os
import re
import pyotp
import random
import functools
import time
import csv

from argparse import ArgumentParser
from urllib.parse import urlparse
from dataclasses import dataclass
from functools import wraps
from typing import Callable, TypeVar, Any, Dict, List, Optional
from pathlib import Path
from playwright.sync_api import (
    expect,
    Page,
    ElementHandle,
    Locator,
    TimeoutError as PlaywrightTimeoutError,
)
from scrapling.engines.toolbelt.custom import Response, Selector
from scrapling.fetchers import StealthySession
from scrapling.cli import log
from datetime import datetime

from casino import (
    CLICK_TIMEOUT_MS,
    MAX_CLICK_RETRIES,
    HANG_DETECTION_SECONDS,
    MAX_KENO_ITERATIONS,
)
from casino import (
    get_credentials,
    google_oauth_login_page_make,
    load_env_file,
    wait_for_load_all_safe,
    wait_for_clickable,
    safe_click,
    url_to_env_prefix,
    gaussian_random_delay,
)

PICKS: List["Pick"] = []
box_selector = "div #cf_turnstile"
button_selector = "button[id='process_claim_hourly_faucet']"


T = TypeVar("T")


@dataclass
class AccountState:
    """Represents the current state of a faucet account."""

    balance: float = 0.0
    wagered: float = 0.0
    target: float = 0.0
    remaining_claims: int = 0
    free_spins: int = 0
    time_remaining: Optional[str] = None  # Format: "MM:SS" when countdown is active

    def is_faucet_available(self) -> bool:
        """Check if the faucet is currently available to claim."""
        return self.time_remaining is None

    def to_dict(self) -> Dict[str, Any]:
        """Convert AccountState to a JSON-serializable dictionary."""
        return {
            "balance": self.balance,
            "wagered": self.wagered,
            "target": self.target,
            "remaining_claims": self.remaining_claims,
            "free_spins": self.free_spins,
            "time_remaining": self.time_remaining,
        }


@dataclass
class BetHistoryEntry:
    """Represents a single bet history entry from game_history.php."""
    time: str          # e.g. "2024-01-15 14:30:22"
    game: str          # e.g. "Keno", "Dice"
    bet: float         # wager amount
    multiplier: float  # e.g. 0.00, 2.50
    profit: float      # win/loss amount


class GameConfig:
    """Configuration for a specific game on the pick site."""

    def __init__(
        self,
        name: str,
        game_url: str,
        play_action: Callable[[Page], Any],
        min_bet: float = 0.0001,
    ):
        self.name = name
        self.game_url = game_url
        self.min_bet = min_bet
        self.play_action = play_action

    def __str__(self):
        return f"GameConfig(name={self.name}, game_url={self.game_url})"

    def __repr__(self):
        return self.__str__()


GAME_CONFIGS_BY_NAME_AND_SITE: Dict[str, "GameConfig"] = {
    "Litepick Keno": GameConfig(
        name="Litepick Keno",
        game_url="https://litepick.io/keno.php",
        min_bet=0.000001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Tronpick Keno": GameConfig(
        name="Tronpick Keno",
        game_url="https://tronpick.io/keno.php",
        min_bet=0.00001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Polpick Keno": GameConfig(
        name="Polpick Keno",
        game_url="https://polpick.io/keno.php",
        min_bet=0.00001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Bnbpick Keno": GameConfig(
        name="Bnbpick Keno",
        game_url="https://bnbpick.io/keno.php",
        min_bet=0.000001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Dogepick Keno": GameConfig(
        name="Dogepick Keno",
        game_url="https://dogepick.io/keno.php",
        min_bet=0.001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Solpick Keno": GameConfig(
        name="Solpick Keno",
        game_url="https://solpick.io/keno.php",
        min_bet=0.000001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Suipick Keno": GameConfig(
        name="Suipick Keno",
        game_url="https://suipick.io/keno.php",
        min_bet=0.00001,
        play_action=lambda page: play_keno_func(page),
    ),
    "Tonpick Keno": GameConfig(
        name="Tonpick Keno",
        game_url="https://tonpick.game/keno.php",
        min_bet=0.00001,
        play_action=lambda page: play_keno_func(page),
    ),
}


def url_to_game_config(url: str) -> Optional["GameConfig"]:
    """Create a GameConfig from a game URL."""
    parsed_url = urlparse(url)
    netloc = parsed_url.netloc

    prefix = netloc.split(".")[0]
    directory = parsed_url.path.strip("/").split("/")[0]
    name = f"{prefix.capitalize()} {directory.capitalize()}"
    return GAME_CONFIGS_BY_NAME_AND_SITE.get(name)


class Pick:
    url: str
    currency: str
    history: list[tuple[datetime, float, float, float, int, int]] = []
    last_update: datetime | None = None
    balance: float = 0.0
    wagered: float = 0.0
    target: float = 0.0
    remaining_claims: int = 0
    free_spins: int = 0
    cooldown_timer: Optional[str] = None

    def __init__(self, url: str, currency: str):
        self.url = url
        self.currency = currency
        self.history = []

    def __str__(self):
        if self.last_update is None:
            return "{: <21} | {: <8} | {: >15} | {: >15} | {: >15} | {: >15} | {: >8} | {: >8} | {: > 15}".format(
                self.url,
                self.currency,
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
            )

        if self.history:
            diff = self.balance - self.history[-1][1]
        else:
            diff = 0.0
        return "{: <21} | {: <8} | {: >15} | {: >15} | {: >15} | {: >15} | {: >8} | {: >12} | {: >15}".format(
            self.url,
            self.currency,
            format(self.balance, ".8f"),
            format(self.wagered, ".8f"),
            format(self.target, ".8f"),
            format(diff, ".8f"),
            str(self.remaining_claims),
            str(self.free_spins),
            str(self.cooldown_timer) if self.cooldown_timer else "N/A",
        )

    def update(
        self,
        balance: Optional[float] = None,
        wagered: Optional[float] = None,
        target: Optional[float] = None,
        remaining_claims: Optional[int] = None,
        free_spins: Optional[int] = None,
        cooldown_timer: Optional[str] = None,
        account_state: Optional[AccountState] = None,
    ) -> None:
        """Update the Pick with new account state data.

        Args:
            balance: The account balance (ignored if account_state is provided)
            wagered: The amount wagered (ignored if account_state is provided)
            target: The wagering target (ignored if account_state is provided)
            remaining_claims: Number of remaining claims (ignored if account_state is provided)
            free_spins: Number of free spins (ignored if account_state is provided)
            account_state: An AccountState object containing all values (preferred method)
        """
        # If AccountState is provided, use it; otherwise use individual parameters
        if account_state is not None:
            balance = account_state.balance
            wagered = account_state.wagered
            target = account_state.target
            remaining_claims = account_state.remaining_claims
            free_spins = account_state.free_spins
            cooldown_timer = account_state.time_remaining
        elif balance is None or wagered is None or target is None:
            raise ValueError(
                "Either account_state or all individual parameters must be provided"
            )

        # Default values for optional parameters
        if remaining_claims is None:
            remaining_claims = 0
        if free_spins is None:
            free_spins = 0

        if self.last_update is not None:
            self.history.append(
                (
                    self.last_update,
                    self.balance,
                    self.wagered,
                    self.target,
                    self.remaining_claims,
                    self.free_spins,
                )
            )

        self.balance = balance
        self.wagered = wagered
        self.target = target
        self.remaining_claims = remaining_claims
        self.free_spins = free_spins
        self.last_update = datetime.now()
        self.cooldown_timer = cooldown_timer

    def get_history(self) -> list[tuple[datetime, float, float, float, int, int]]:
        return (
            self.history
            + [
                (
                    self.last_update,
                    self.balance,
                    self.wagered,
                    self.target,
                    self.remaining_claims,
                    self.free_spins,
                )
            ]
            if self.last_update
            else self.history
        )

    def get_env_prefix(self) -> str:
        return url_to_env_prefix(self.url)

    def to_dict(self) -> Dict[str, Any]:
        return pick_to_dict_json_safe(self)

    def write(self, filepath: str):
        import json

        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=4)

    @staticmethod
    def from_dict(data):
        return json_to_pick(data)

    @staticmethod
    def read(filepath: str):
        import json

        with open(filepath, "r") as f:
            data = json.load(f)
            return Pick.from_dict(data)


def get_balance(page: Page) -> float:
    """Get the current balance from the page.

    Args:
        page (Page): The Playwright page object.
    Returns:
        float: The current balance.
    """
    balance_selector = "body > header > nav > div.navbar-header > div > div > span"
    balance = 0.0

    balance_element: Optional[ElementHandle] = page.query_selector(
        selector=balance_selector
    )

    if balance_element:
        balance_text = balance_element.text_content()
        try:
            balance = float("".join(balance_text.split()).replace(",", ""))
        except ValueError:
            log.error("Could not parse balance: %s", balance_text)

    return balance


def bet_and_start_auto(
    page: Page, stop: bool = False, min_bet: float = 0.00000100
) -> Optional[float]:
    """Stop any ongoing betting, rebet at 1/100 of balance, and start auto betting on the page.

    Args:
        page (Page): The Playwright page object.
        stop (bool, optional): Whether to stop existing autobet first. Defaults to False.
        min_bet (float, optional): Minimum bet allowed for this game. Defaults to 0.00000100.

    Returns:
        Optional[float]: The wager amount if successful, None if any operation failed.
    """
    try:
        balance = get_balance(page)
        wager_amount = max(
            min_bet, balance / 100.0
        )  # Bet 1/100th of balance or min_bet, whichever is greater

        # Stop existing autobet if requested
        if stop:
            if not safe_click(page, "#stop_autobet", timeout=CLICK_TIMEOUT_MS):
                log.error("Failed to stop autobet")
                return None

        # Fill in bet amount
        try:
            page.fill(
                "#bet_amount",
                str(format(wager_amount, ".8f")),
                timeout=CLICK_TIMEOUT_MS,
            )
        except Exception as e:
            log.error("Failed to fill bet amount: %s", e)
            return None

        # Click the difficulty selector (hard mode)
        if not safe_click(page, "#hard", timeout=CLICK_TIMEOUT_MS, force=True):
            log.error("Failed to click hard mode selector")
            return None

        # Enable autobet mode switch
        try:
            page.check("#switch_bet_mode", force=True, timeout=CLICK_TIMEOUT_MS)
        except Exception as e:
            log.error("Failed to enable autobet mode switch: %s", e)
            return None

        # Start autobet
        if not safe_click(page, "#start_autobet", timeout=CLICK_TIMEOUT_MS, force=True):
            log.error("Failed to start autobet")
            return None

        log.info("Successfully started autobet with wager amount: %f", wager_amount)
        return wager_amount

    except Exception as e:
        log.error("Unexpected error in bet_and_start_auto: %s", e)
        return None


def play_keno_func(page: Page) -> None:
    """Play keno game on pick sites with hang detection."""

    # Get the game config from the current URL
    current_url = page.url
    game_config = url_to_game_config(current_url)

    if game_config:
        min_bet = game_config.min_bet
        log.info("Using game config: %s with min_bet: %f", game_config.name, min_bet)
    else:
        min_bet = 0.00000100
        log.warning(
            "No game config found for URL: %s, using default min_bet: %f",
            current_url,
            min_bet,
        )

    # generate 7 random picks between 1 and 40
    picks = random.sample(range(1, 41), 7)
    board_selector = "#keno_table > div.keno_gamecell > div.keno_gamecell_index"

    # Select keno board picks with timeout handling
    for pick in picks:
        try:
            page.locator(board_selector).filter(has_text=str(pick)).first.click(
                delay=gaussian_random_delay(), timeout=CLICK_TIMEOUT_MS
            )
        except Exception as e:
            log.error("Failed to click keno pick %d: %s", pick, e)
            return

    # Start autobet with timeout handling, using the game-specific min_bet
    wager_amount = bet_and_start_auto(page, min_bet=min_bet)
    if wager_amount is None:
        log.error("Failed to start autobet, exiting keno game")
        return

    # Initialize hang detection variables
    last_balance = get_balance(page)
    last_balance_change_time = time.time()
    iteration_count = 0

    # Loop every 5 seconds and check balance
    while True:
        iteration_count += 1

        # Safety net: exit after maximum iterations
        if iteration_count > MAX_KENO_ITERATIONS:
            log.warning(
                "Reached maximum iterations (%d), exiting keno game",
                MAX_KENO_ITERATIONS,
            )
            break

        balance = get_balance(page)
        current_time = time.time()

        # Check if balance has changed (indicating game is still running)
        if balance != last_balance:
            last_balance = balance
            last_balance_change_time = current_time
            log.info("Current balance: %f", balance)
        else:
            # Check if we've been stuck too long
            time_since_change = current_time - last_balance_change_time
            if time_since_change > HANG_DETECTION_SECONDS:
                log.error(
                    "Balance unchanged for %d seconds (balance: %f), detected hang - exiting",
                    int(time_since_change),
                    balance,
                )
                break

        # Check if balance is too low to continue (below 20x the bet amount)
        if balance < wager_amount * 20:
            log.info(
                "Balance too low (%f < %f), stopping keno game.",
                balance,
                wager_amount * 20,
            )
            # Try to stop autobet before exiting
            try:
                safe_click(page, "#stop_autobet", timeout=CLICK_TIMEOUT_MS)
            except Exception as e:
                log.warning("Could not stop autobet: %s", e)
            break

        # Check if balance grew significantly - rebet at new level (1% of balance)
        # Only rebet if balance is more than 300x current wager
        elif balance > wager_amount * 300:
            log.info(
                "Balance grew significantly (%f > %f), rebetting at higher amount.",
                balance,
                wager_amount * 300,
            )
            new_wager = bet_and_start_auto(page, stop=True, min_bet=min_bet)
            if new_wager is None:
                log.error("Failed to rebet, exiting keno game")
                break
            wager_amount = new_wager
            # Reset hang detection after successful rebet
            last_balance = balance
            last_balance_change_time = time.time()

        # Check if balance is extremely high - stop to prevent further risk
        elif balance > wager_amount * 1000:
            log.info(
                "Balance extremely high (%f > %f), stopping keno game to secure profits.",
                balance,
                wager_amount * 1000,
            )
            # Try to stop autobet before exiting
            try:
                safe_click(page, "#stop_autobet", timeout=CLICK_TIMEOUT_MS)
            except Exception as e:
                log.warning("Could not stop autobet: %s", e)
            break

        page.wait_for_timeout(5000)  # Wait for 5 seconds to let the game play out

    log.info("Keno game ended.")


def default_picks() -> list[Pick]:
    """Return the default list of Pick objects.
    Returns:
        list[Pick]: List of default Pick objects.
    """
    return [
        Pick(url="https://tronpick.io/", currency="TRX"),
        Pick(url="https://litepick.io/", currency="LTC"),
        Pick(url="https://polpick.io/", currency="POL"),
        Pick(url="https://bnbpick.io/", currency="BNB"),
        Pick(url="https://dogepick.io/", currency="DOGE"),
        Pick(url="https://solpick.io/", currency="SOL"),
        Pick(url="https://suipick.io/", currency="SUI"),
        Pick(url="https://tonpick.game/", currency="TON"),
    ]


def screenshot_action(func: Callable[[Page], T]) -> Callable[[Page], T]:
    """Decorator that takes before/after screenshots using the decorated function's name and currency from closure.

    This decorator preserves the return type of the decorated function.

    Args:
        func (Callable[[Page], T]): The function to decorate.
    Returns:
        Callable[[Page], T]: The wrapped function with screenshot functionality.
    """

    @functools.wraps(func)
    def wrapper(page: Page) -> T:
        """
        Wrapper function that takes screenshots before and after executing the original function.
        Args:
            page (Page): The Playwright page object.
        Returns:
            T: The return value of the original function.
        """
        enable_screenshots: bool = False
        enable_states: bool = True

        # Get the function name (be defensive: some call sites may accidentally
        # pass non-callables like sentinel objects)
        func_name = getattr(func, "__name__", func.__class__.__name__)

        # Extract currency from the closure variables
        currency = "UNK"
        if hasattr(func, "__closure__") and func.__closure__:
            # Map closure variable names to their values
            closure_vars = func.__code__.co_freevars if hasattr(func, "__code__") else ()
            for i, var_name in enumerate(closure_vars):
                if var_name == "currency":
                    currency = func.__closure__[i].cell_contents
                elif var_name == "enable_screenshots":
                    enable_screenshots = func.__closure__[i].cell_contents
                elif var_name == "enable_states":
                    enable_states = func.__closure__[i].cell_contents

        # Create base filename with timestamp
        timestamp = int(time.time() * 1000)  # milliseconds for uniqueness

        if enable_screenshots:
            # Ensure screenshots directory exists
            screenshot_dir = Path("screenshots")
            screenshot_dir.mkdir(exist_ok=True)

            # Take BEFORE screenshot
            before_filename = f"{func_name}_{currency}_{timestamp}_before.png"
            page.screenshot(path=str(screenshot_dir / before_filename))
        else:
            log.info("Screenshots disabled, skipping before/after screenshots.")

        # Execute the original function
        if not callable(func):
            raise TypeError(
                f"screenshot_action expected a callable, got {type(func)!r} ({func!r})"
            )
        result = func(page)

        # Get the account state from the live page.
        if enable_states:
            account_state = parse_account_state_page(page, currency)
            if account_state:
                PICKS[currency].update(account_state=account_state)

        if enable_screenshots:
            # Take AFTER screenshot
            after_filename = f"{func_name}_{currency}_{timestamp}_after.png"
            page.screenshot(path=str(screenshot_dir / after_filename))
        else:
            log.info("Screenshots disabled, skipping before/after screenshots.")

        return result

    return wrapper


def parse_flipclock(
    page: Page, flipclock_selector: str, flipclock_digits_selector: str
) -> Optional[str]:
    """Parse the flipclock countdown timer from the page.

    Args:
        page (Page): The Playwright Page object.
        flipclock_selector (str): The CSS selector for the flipclock container.
        flipclock_digits_selector (str): The CSS selector for the flipclock digit elements.

    Returns:
        Optional[str]: The time remaining in MM:SS format, or None if not found.
    """
    time_remaining = None
    try:
        # Wait for flipclock to be populated by JavaScript (max 3 seconds)
        flipclock_locator: Locator = page.locator(flipclock_selector)
        expect(flipclock_locator).to_be_attached(timeout=3000)
        flipclock_element: ElementHandle = flipclock_locator.element_handle()
    except PlaywrightTimeoutError as e:
        log.warning(
            "Flipclock not found on page (faucet likely ready): %s", str(e)[:100]
        )
        # Without ``flipclock_element`` the rest of this function would hit
        # ``UnboundLocalError``. Faucet-ready is the expected case here, so
        # we surface ``None`` (the documented "no time remaining" signal)
        # rather than raising.
        return None

    # Query the flipclock digits from the live DOM
    active_digits_elements: List[ElementHandle] = flipclock_element.query_selector_all(
        flipclock_digits_selector
    )

    log.info("Found %d active digit elements from Page", len(active_digits_elements))

    if active_digits_elements and len(active_digits_elements) >= 4:
        # Extract text from each digit element
        digits_text = [elem.inner_text().strip() for elem in active_digits_elements[:4]]
        log.info("Digit texts from Page: %s", digits_text)

        try:
            # Each element should contain a single digit
            minute_tens = digits_text[0][0] if digits_text[0] else "0"
            minute_ones = digits_text[1][0] if digits_text[1] else "0"
            second_tens = digits_text[2][0] if digits_text[2] else "0"
            second_ones = digits_text[3][0] if digits_text[3] else "0"

            time_remaining = f"{minute_tens}{minute_ones}:{second_tens}{second_ones}"
            log.info("Flipclock time remaining (from Page): %s (MM:SS)", time_remaining)
        except (IndexError, AttributeError) as e:
            log.warning("Failed to parse flipclock digits from Page: %s", e)
    else:
        log.info("active_digits_elements: %s", active_digits_elements)
        log.info("No active flipclock found - faucet may be ready to claim")

    return time_remaining


def parse_account_state_page(
    page: Page, currency: str = "UNK"
) -> Optional[AccountState]:
    """Parse the current account state from the faucet page.

    Extracts balance, wagering progress, remaining claims, free spins, and countdown timer
    information from the page.

    Args:
        page (Page): The Playwright Page object for querying dynamic content. Defaults to None.
        currency (str, optional): The currency code for logging. Defaults to "UNK".

    Returns:
        AccountState: An AccountState object containing all parsed account information.
    """
    flipclock_selector: str = "#faucet_countdown_clock"
    flipclock_digits_selector: str = ".clock ul.flip"
    time_remaining: Optional[str] = None
    balance_element: Optional[ElementHandle] = page.query_selector(
        selector="span[class=user_balance]"
    )
    balance_element_new: Optional[ElementHandle] = page.query_selector(
        selector=".drop_down_header_text"
    )
    wagered_element: Optional[ElementHandle] = page.query_selector(
        selector="b[id=total_wagered]"
    )
    target_element: Optional[ElementHandle] = page.query_selector(
        selector="b[id=wagering_target]"
    )
    remaining_claims_element: Optional[ElementHandle] = page.query_selector(
        selector="b[class=faucet_claims_remaining]"
    )
    free_spins_element: Optional[ElementHandle] = page.query_selector(
        selector="span[id=free_spins]"
    )

    # If none of the faucet-specific elements exist, we're not on a page that
    # exposes account state (e.g. login.php or the post-login landing page).
    # Returning a zero-filled AccountState here would overwrite real state in
    # Pick.update() and poison history with bogus [0, 0, 0, 0, 0] entries.
    if (
        wagered_element is None
        and target_element is None
        and remaining_claims_element is None
    ):
        log.debug(
            "[%s] No faucet state elements found on page %s; skipping state parse",
            currency,
            page.url,
        )
        return None

    flipclock_locator: Locator = page.locator(flipclock_selector)

    if flipclock_locator.count() > 0:
        time_remaining = parse_flipclock(
            page, flipclock_selector, flipclock_digits_selector
        )

    balance_text = balance_element.text_content() if balance_element else None
    balance_text_new = (
        balance_element_new.text_content() if balance_element_new else None
    )
    wagered_text = wagered_element.text_content() if wagered_element else "0.0"
    target_text = target_element.text_content() if target_element else "0.0"
    remaining_claims_text = (
        remaining_claims_element.text_content() if remaining_claims_element else "0"
    )
    free_spins_text = free_spins_element.text_content() if free_spins_element else "0"

    # Parse numeric values
    try:
        balance = (
            float("".join(balance_text.split()).replace(",", ""))
            if balance_text
            else (
                float("".join(balance_text_new.split()).replace(",", ""))
                if balance_element_new
                else 0.0
            )
        )
        wagered = float(wagered_text.strip())
        target = float(target_text.strip())
        remaining_claims = int(remaining_claims_text.strip())
        free_spins = int(free_spins_text.strip())
    except ValueError as e:
        log.error("Error parsing numeric values: %s", e)
        return None

    return AccountState(
        balance=balance,
        wagered=wagered,
        target=target,
        remaining_claims=remaining_claims,
        free_spins=free_spins,
        time_remaining=time_remaining,
    )


def parse_account_state_res(res: Response, currency: str = "UNK") -> AccountState:
    """Parse the current account state from the faucet page response.

    Extracts balance, wagering progress, remaining claims, free spins, and countdown timer
    information from the page response.

    Args:
        res (Response): The Response object from the faucet page.
        currency (str, optional): The currency code for logging. Defaults to "UNK".

    Returns:
        AccountState: An AccountState object containing all parsed account information.
    """
    # Select all account state elements
    balance_selector = res.css(
        selector="span[class=user_balance]", identifier=f"balance_{currency}"
    )
    wagered_selector = res.css(
        selector="b[id=total_wagered]", identifier=f"wagered_{currency}"
    )
    target_selector = res.css(
        selector="b[id=wagering_target]", identifier=f"target_{currency}"
    )
    remaining_claims_selector = res.css(
        selector="b[class=faucet_claims_remaining]",
        identifier=f"remaining_claims_{currency}",
    )
    free_spins_selector = res.css(
        selector="span[id=free_spins]", identifier=f"free_spins_{currency}"
    )
    countdown_selector = res.css(
        selector='div[id="faucet_countdown_clock"]', identifier=f"countdown_{currency}"
    )

    # Extract text values with defaults
    balance_text = balance_selector.get().text if balance_selector.get() else "0.0"
    wagered_text = wagered_selector.get().text if wagered_selector.get() else "0.0"
    target_text = target_selector.get().text if target_selector.get() else "0.0"
    remaining_claims_text = (
        remaining_claims_selector.get().text if remaining_claims_selector.get() else "0"
    )
    free_spins_text = (
        free_spins_selector.get().text if free_spins_selector.get() else "0"
    )

    # Parse numeric values
    balance = float(balance_text.strip().replace(",", "").strip())
    wagered = float(wagered_text.strip())
    target = float(target_text.strip())
    remaining_claims = int(remaining_claims_text.strip())
    free_spins = int(free_spins_text.strip())

    # Parse countdown timer if active
    time_remaining: Optional[str] = None

    # If Page object is provided, use it to query the live DOM (with JavaScript-generated content)
    # Fallback to Response object parsing (may not work for JavaScript-generated content)
    countdown_element: Selector = countdown_selector.get()
    log.info(
        "countdown_element exists: %s (using Response fallback)",
        countdown_element is not None,
    )
    if countdown_element:
        # Parse the flipclock value - extract minutes and seconds from the flip clock
        active_digits_selector = res.css(
            selector="#faucet_countdown_clock li.flip-clock-active",
            identifier=f"flipclock_digits_{currency}",
        )

        active_digits = active_digits_selector.get_all()
        log.info(
            "Found %d active digit elements (from Response)",
            len(active_digits) if active_digits else 0,
        )

        if active_digits and len(active_digits) >= 4:
            # Extract the 4 digits: MM:SS
            try:
                minute_tens = active_digits[0].text.strip()[0]
                minute_ones = active_digits[1].text.strip()[0]
                second_tens = active_digits[2].text.strip()[0]
                second_ones = active_digits[3].text.strip()[0]

                time_remaining = (
                    f"{minute_tens}{minute_ones}:{second_tens}{second_ones}"
                )
                log.info(
                    "Flipclock time remaining (from Response): %s (MM:SS)",
                    time_remaining,
                )
            except (IndexError, AttributeError) as e:
                log.warning("Failed to parse flipclock digits: %s", e)
        else:
            log.info("No flipclock digits found - faucet likely ready to claim")

    return AccountState(
        balance=balance,
        wagered=wagered,
        target=target,
        remaining_claims=remaining_claims,
        free_spins=free_spins,
        time_remaining=time_remaining,
    )


def login_page_make(
    username: str,
    password: str,
    currency: str = "UNK",
    enable_screenshots: bool = False,
) -> tuple[Callable[[Page], None], Callable[[], bool]]:
    """Create a login page action.

    Args:
        username (str): Username for login.
        password (str): Password for login.
        currency (str, optional): The currency code. Defaults to "UNK".
        enable_screenshots (bool, optional): Enable screenshots. Defaults to False.

    Returns:
        tuple[Callable[[Page], None], Callable[[], bool]]: A tuple containing:
            - The login page action function
            - A function that returns True if claim was already attempted during login
    """
    claim_attempted = {"value": False}  # Use dict for mutability in closure

    @screenshot_action
    def login_page(page: Page):
        """
        Perform login on the given page.
        Args:
            page (Page): The Playwright page object.
        Returns:
            None
        """
        _ = currency  # Force currency into closure for screenshot_action decorator
        _ = enable_screenshots  # Force enable_screenshots into closure for screenshot_action decorator
        login_button_selector = "button[id='process_login']"

        if "login" not in page.url:
            log.warning("Not on login page, current URL: %s", page.url)
            # log.warning("Attempting to click the claim button, assuming already logged in.")
            # page.click(button_selector)
            # claim_attempted["value"] = True
            return

        page.fill("input[id='user_email']", username)
        page.fill("input[id='password']", password)

        try:
            page.locator(box_selector).scroll_into_view_if_needed(timeout=2000)
            page.locator(box_selector).wait_for(state="visible", timeout=2000)
            log.debug("Turnstile box detected.")
            wait_for_load_all_safe(page)
        except Exception:
            log.debug("No turnstile box detected.")

        page.click(login_button_selector, delay=gaussian_random_delay())

    def was_claim_attempted() -> bool:
        """Check if the claim button was already clicked during login.

        Returns:
            bool: True if claim was attempted, False otherwise.
        """
        return claim_attempted["value"]

    return login_page, was_claim_attempted


def make_claim_faucet(
    selector: str, currency: str = "UNK", enable_screenshots: bool = False
) -> Callable[[Page], None]:
    """Create a claim faucet action.

    Args:
        selector (str): The CSS selector for the claim button.
        currency (str, optional): The currency code. Defaults to "UNK".
        enable_screenshots (bool, optional): Enable screenshots. Defaults to False.
    Returns:
        Callable[[Page], None]: The claim faucet action function.
    """

    @screenshot_action
    def claim_faucet(page: Page):
        """
        Perform claim action on the given page.
        Args:
            page (Page): The Playwright page object.
        Returns:
            None
        """
        _ = currency  # Force currency into closure for screenshot_action decorator
        _ = enable_screenshots  # Force enable_screenshots into closure for screenshot_action decorator
        try:
            page.locator(selector).scroll_into_view_if_needed(timeout=2000)
            page.locator(selector).wait_for(state="visible", timeout=2000)
            page.wait_for_timeout(
                gaussian_random_delay(mean=500, stddev=100)
            )  # Wait for some time, because.
        except Exception as e:
            log.info("No box detected: %s", e)
            return

        delay = gaussian_random_delay()
        page.click(selector, delay=delay)

    return claim_faucet


def make_bonus_rolls_faucet(
    tab_selector: str = "div.faucet-tabs",
    roll_selector: str = "#process_claim_bonus_faucet",
    currency: str = "UNK",
    enable_screenshots: bool = False,
    max_rolls: int = 300,
    wait_between_ms: int = 1000,
) -> Callable[[Page], int]:
    """Create an action that navigates to the bonus tab and performs repeated bonus rolls.

    This action will:
    - Click the tab (if present) to reveal the bonus rolls UI
    - Repeatedly click the roll button up to `max_rolls` times
    - Wait a randomized delay between rolls (centered on `wait_between_ms`)
    - Stop early if the roll button disappears or if free spins drop to zero (if detectable)

    Returns:
        Callable[[Page], int]: A page action that returns the number of rolls performed.
    """

    @screenshot_action
    def bonus_rolls_action(page: Page) -> int:
        _ = currency
        _ = enable_screenshots

        rolls_done = 0

        try:
            # Try to open the bonus tab if a tab selector is provided
            if tab_selector:
                try:
                    tab = page.locator(tab_selector).first
                    if tab.count() > 0:
                        tab.click(delay=gaussian_random_delay(), timeout=3000)
                        page.wait_for_timeout(500)
                except Exception:
                    log.debug("Bonus tab not found or not clickable: %s", tab_selector)

            for i in range(max_rolls):
                # Try to find the roll button
                page.wait_for_timeout(gaussian_random_delay(mean=1000, stddev=100))
                try:
                    roll_btn = page.locator(roll_selector).first
                    roll_count = roll_btn.count()
                except Exception:
                    log.debug("Error locating roll button: %s", roll_selector)
                    break

                if not roll_count or roll_count == 0:
                    log.info("No roll button available (stopping).")
                    break

                # Click the roll button
                try:
                    delay = gaussian_random_delay()
                    roll_btn.click(delay=delay)
                    rolls_done += 1
                    log.info("Performed bonus roll %d/%d", rolls_done, max_rolls)
                    first = page.query_selector(".roll_numbers .first_digit")
                    second = page.query_selector(".roll_numbers .second_digit")
                    third = page.query_selector(".roll_numbers .third_digit")
                    fourth = page.query_selector(".roll_numbers .fourth_digit")
                    fifth = page.query_selector(".roll_numbers .fifth_digit")
                    result = first.inner_text() + second.inner_text() + third.inner_text() + fourth.inner_text() + fifth.inner_text()
                    log.info("Roll result: %s", result)
                except Exception as e:
                    log.warning("Failed to click roll button: %s", e)
                    break

                # Wait a bit for UI to update and to be polite
                wait_ms = int(max(0, random.gauss(wait_between_ms, max(1, wait_between_ms * 0.1))))
                page.wait_for_timeout(wait_ms)

                # If we can detect free_spins on the page, stop if zero
                try:
                    free_spins_element: Optional[ElementHandle] = page.query_selector(
                        "span[id=free_spins]"
                    )
                    if free_spins_element:
                        text = free_spins_element.text_content() or "0"
                        try:
                            remaining = int(text.strip())
                            if remaining <= 0:
                                log.info("No remaining free spins detected (stopping).")
                                break
                        except Exception:
                            # could not parse number; continue
                            pass
                except Exception:
                    pass

            log.info("Finished bonus rolls; total performed: %d", rolls_done)
        except Exception:
            log.exception("Unexpected error during bonus rolls action")

        return rolls_done

    return bonus_rolls_action


def prepare_user_data_dir(user_data_dir: str) -> tuple[str, bool]:
    """If the Firefox profile is locked (browser running), copy it to a temp dir.

    Returns:
        tuple: (path_to_use, is_temp) - the path to use and whether it's a temp copy.
    """
    import shutil
    import tempfile

    profile_path = Path(user_data_dir)
    lock_file = profile_path / "lock"

    if lock_file.exists() or lock_file.is_symlink():
        log.info(
            "Firefox profile is locked (browser running). "
            "Copying profile to temp directory..."
        )
        temp_dir = tempfile.mkdtemp(prefix="pick_profile_")
        # Copy only essential dirs/files for IndexedDB access
        for item in ["storage", "storage.sqlite", "prefs.js", "permissions.sqlite"]:
            src = profile_path / item
            dst = Path(temp_dir) / item
            if src.exists():
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        log.info("Profile copied to %s", temp_dir)
        return temp_dir, True

    return user_data_dir, False


def make_scrape_bet_history_action(
    currency: str = "UNK",
) -> tuple[Callable[[Page], None], list["BetHistoryEntry"]]:
    """Create a page_action that extracts bet history from localforage.

    These pick sites store bet history client-side in the browser's IndexedDB
    via localforage (keys: 'my_bet_data_large' and 'my_bet_data'). This requires
    --user-data-dir pointing to the user's actual browser profile.

    Returns:
        tuple: (page_action_callable, shared_entries_list)
            The page_action appends parsed entries to the shared list.
    """
    entries: list[BetHistoryEntry] = []

    def scrape_bet_history_action(page: Page) -> None:
        # Read bet data directly from IndexedDB using native browser API
        # (avoids depending on the localforage JS library being loaded)
        bet_data = page.evaluate("""() => {
            function readKey(db, key) {
                return new Promise((resolve, reject) => {
                    try {
                        const tx = db.transaction('keyvaluepairs', 'readonly');
                        const store = tx.objectStore('keyvaluepairs');
                        const req = store.get(key);
                        req.onsuccess = () => resolve(req.result);
                        req.onerror = () => resolve(null);
                    } catch(e) { resolve(null); }
                });
            }
            return new Promise((resolve) => {
                const req = indexedDB.open('localforage');
                req.onsuccess = async (event) => {
                    const db = event.target.result;
                    const large = await readKey(db, 'my_bet_data_large');
                    const small = await readKey(db, 'my_bet_data');
                    db.close();
                    resolve({large: large, small: small});
                };
                req.onerror = () => resolve(null);
                // If the DB doesn't exist, onupgradeneeded fires for version 1
                req.onupgradeneeded = (event) => {
                    // DB is empty/new, no data to read
                    event.target.transaction.abort();
                    resolve(null);
                };
            });
        }""")

        if not bet_data:
            log.warning("[%s] Could not open localforage IndexedDB", currency)
            return

        large_data = bet_data.get("large")
        small_data = bet_data.get("small")

        # Use whichever has more data, preferring large
        raw_entries = large_data or small_data or []

        if not raw_entries:
            log.warning(
                "[%s] No bet data found in localforage. "
                "Make sure --user-data-dir points to your browser profile "
                "that has the bet history.",
                currency,
            )
            return

        log.info("[%s] Found %d entries in localforage", currency, len(raw_entries))

        for item in raw_entries:
            try:
                # localforage entries have: game_name, bet_amount, payout, profit,
                # server_seed, game_id, and optionally timestamp/time
                game_name = item.get("game_name", "Unknown")
                bet_amount = str(item.get("bet_amount", "0"))
                payout = str(item.get("payout", "0"))
                profit = str(item.get("profit", "0"))
                time_val = item.get("time", item.get("timestamp", ""))
                # Convert Unix timestamp to readable datetime
                try:
                    time_str = datetime.fromtimestamp(int(time_val)).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError, OSError):
                    time_str = str(time_val)

                bet_val = float(bet_amount.replace(",", "").strip())
                # payout field contains the multiplier display (e.g. "4.02×")
                payout_str = payout.replace("\u00d7", "").replace("x", "").replace(",", "")
                # Strip any HTML color spans
                payout_clean = re.sub(r'<[^>]+>', '', payout_str).strip()
                multiplier_val = float(payout_clean) if payout_clean else 0.0
                # Same for profit
                profit_clean = re.sub(r'<[^>]+>', '', profit.replace(",", "")).strip()
                profit_val = float(profit_clean) if profit_clean else 0.0

                entries.append(BetHistoryEntry(
                    time=time_str,
                    game=game_name,
                    bet=bet_val,
                    multiplier=multiplier_val,
                    profit=profit_val,
                ))
            except (ValueError, TypeError, KeyError) as e:
                log.debug("[%s] Skipping entry: %s (data: %s)", currency, e, str(item)[:100])
                continue

        log.info("[%s] Parsed %d bet history entries from localforage", currency, len(entries))

    return scrape_bet_history_action, entries


def save_bet_history_csv(entries: list["BetHistoryEntry"], currency: str) -> str:
    """Save bet history entries to a CSV file, merging with existing data.

    Args:
        entries: List of BetHistoryEntry objects to save.
        currency: Currency code used for the filename.

    Returns:
        str: Path to the saved CSV file.
    """
    directory = Path("bet_history")
    directory.mkdir(exist_ok=True)

    prefix = currency.upper()
    filepath = directory / f"{prefix}.csv"

    # Load existing entries for dedup
    existing_keys: set[str] = set()
    existing_rows: list[BetHistoryEntry] = []
    if filepath.exists():
        with open(filepath, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    entry = BetHistoryEntry(
                        time=row["time"],
                        game=row["game"],
                        bet=float(row["bet"]),
                        multiplier=float(row["multiplier"]),
                        profit=float(row["profit"]),
                    )
                    key = f"{entry.time}|{entry.game}|{entry.bet}"
                    if key not in existing_keys:
                        existing_keys.add(key)
                        existing_rows.append(entry)
                except (KeyError, ValueError) as e:
                    log.warning("Skipping malformed CSV row: %s", e)

    # Merge new entries, dedup by time+game+bet
    new_count = 0
    for entry in entries:
        key = f"{entry.time}|{entry.game}|{entry.bet}"
        if key not in existing_keys:
            existing_keys.add(key)
            existing_rows.append(entry)
            new_count += 1

    # Sort by time descending (newest first)
    existing_rows.sort(key=lambda e: e.time, reverse=True)

    # Write all entries
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "game", "bet", "multiplier", "profit"])
        for entry in existing_rows:
            writer.writerow([entry.time, entry.game, entry.bet, entry.multiplier, entry.profit])

    log.info("Saved %d entries (%d new) to %s", len(existing_rows), new_count, filepath)
    return str(filepath)


def pick_to_dict_json_safe(pick_obj: Pick) -> Dict[str, Any]:
    """Convert Pick object to JSON-safe dict

    Args:
        pick_obj (Pick): The Pick object to convert.
    Returns:
        Dict[str, Any]: The JSON-safe dict representation of the Pick object.
    """
    # Skipping the cooldown timer on purpose here because it's an ephemeral attribute.
    data = {
        "url": pick_obj.url,
        "currency": pick_obj.currency,
        "balance": pick_obj.balance,
        "wagered": pick_obj.wagered,
        "history": [
            (dt.isoformat(), balance, wagered, target, remaining_claims, free_spins)
            for dt, balance, wagered, target, remaining_claims, free_spins in pick_obj.history
        ],
        "last_update": pick_obj.last_update.isoformat()
        if pick_obj.last_update
        else None,
        "target": pick_obj.target,
        "remaining_claims": pick_obj.remaining_claims,
        "free_spins": pick_obj.free_spins,
        # "cooldown_timer": pick_obj.cooldown_timer  <-- NO!
    }
    return data


def json_to_pick(data: Dict[str, Any]) -> Pick:
    """Convert JSON-safe dict to Pick object.
    Args:
        data (Dict[str, Any]): The JSON-safe dict representation of the Pick object.
    Returns:
        Pick: The Pick object.
    """
    pick_obj = Pick(url=data["url"], currency=data["currency"])
    pick_obj.balance = data["balance"]
    pick_obj.wagered = data["wagered"]
    pick_obj.history = [
        (
            datetime.fromisoformat(dt),
            balance,
            wagered,
            target,
            remaining_claims,
            free_spins,
        )
        for dt, balance, wagered, target, remaining_claims, free_spins in data[
            "history"
        ]
    ]
    pick_obj.last_update = (
        datetime.fromisoformat(data["last_update"]) if data["last_update"] else None
    )
    pick_obj.target = data["target"]
    pick_obj.remaining_claims = data.get("remaining_claims", 0)
    pick_obj.free_spins = data.get("free_spins", 0)
    return pick_obj


def save_picks(picks: list[Pick], directory: str = "picks_data"):
    """Save the picks to JSON files in the specified directory.
    Args:
        picks (list[Pick]): List of Pick objects to save.
        directory (str, optional): Directory to save the JSON files. Defaults to "picks_data".
    """
    os.makedirs(directory, exist_ok=True)
    for pick in picks:
        filename = f"{pick.get_env_prefix()}.json"
        filepath = os.path.join(directory, filename)
        pick.write(filepath)
        log.info("Saved pick data to %s", filepath)


def load_picks(directory: str = "picks_data") -> list[Pick]:
    """
    Load the picks from JSON files in the specified directory.
    Args:
        directory (str, optional): Directory to load the JSON files from. Defaults to
        "picks_data".
    Returns:
        list[Pick]: List of loaded Pick objects.
    """
    picks = []
    if not os.path.exists(directory):
        return picks

    for filename in os.listdir(directory):
        if filename.endswith(".json"):
            filepath = os.path.join(directory, filename)
            try:
                pick = Pick.read(filepath)
                picks.append(pick)
                log.info("Loaded pick data from %s", filepath)
            except Exception as e:
                log.error("Failed to load pick data from %s: %s", filepath, e)
    return picks


def check_logged_in(res: Response) -> bool:
    """
    Check if the user is logged in.

    Primary signal: the server redirects authenticated users off /login.php
    (usually to /faucet.php). If the final response URL is no longer on the
    login page, the session cookie was accepted. The older DOM-based check
    looked for logout <a>/<li> elements, but those are rendered client-side
    by JS and aren't in the fetched HTML, so it reported "failed" even
    after a successful login.

    DOM-based checks are kept as a fallback for cases where URL info is
    unavailable (e.g. a Response constructed without one).
    """
    url = (getattr(res, "url", None) or "").lower()
    if url and "login.php" not in url:
        return True

    logout_link = res.css(selector="li[class='lg_logout_btn']", identifier="logout_btn")
    logout_link1 = res.css(selector="a#process_logout", identifier="logout_link_class")
    logout_link11 = res.css(selector="#process_logout > li", identifier="logout_link_slide_menu")
    logout_link2 = res.css(selector="a[href*='logout']", identifier="logout_link_href")
    logout_link3 = res.css(selector="a.logout", identifier="logout_link_class").filter(
        lambda el: el.has_text("Logout")
    )

    return (
        logout_link.get() is not None
        or logout_link1.get() is not None
        or logout_link11.get() is not None
        or logout_link2.get() is not None
        or logout_link3.get() is not None
    )


def summarize_picks(picks: List[Pick]) -> None:
    """Summarize the picks by printing a table of their current status.

    Args:
        picks (list[Pick]): List of Pick objects to summarize.
    Returns:
        None (side-effect: prints summary to log)
    """
    log.info(
        "{: <21} | {: <8} | {: >15} | {: >15} | {: >15} | {: >15} | {: >8} | {: >12} | {: >15}".format(
            "URL",
            "Currency",
            "Balance",
            "Wagered",
            "Target",
            "Diff",
            "Claims",
            "Bonus Spins",
            "Cooldown Timer",
        )
    )
    for pick in picks:
        log.info(pick)


@contextlib.contextmanager
def safe_stealthy_session(**kwargs):
    """StealthySession wrapper that swallows cleanup errors.

    Why: if the browser dies mid-fetch (Cloudflare hang that the user kills,
    Playwright TargetClosedError, etc.), the inner exception is caught by
    the caller, but StealthySession.__exit__ -> context.close() then throws
    "Connection closed while reading from the driver" because the driver
    process is already gone. That second exception escapes any try/except
    inside the `with` body and crashes main. Swallowing it lets the loop
    move on to the next pick.
    """
    session = StealthySession(**kwargs)
    session.__enter__()
    try:
        yield session
    finally:
        try:
            session.__exit__(None, None, None)
        except Exception as teardown_err:
            log.warning(
                "Session cleanup failed (browser likely crashed or was killed): %s",
                teardown_err,
            )


def main(
    picks: List[Pick],
    proxy: str | None = None,
    use_google_oauth: bool = False,
    user_data_dir: str | None = None,
    headless: bool = False,
    skip_claim: bool = False,
    play_keno: bool = False,
    summarize: bool = False,
    enable_screenshots: bool = False,
    do_bonus_rolls: bool = False,
    bonus_tab_selector: str | None = None,
    bonus_roll_selector: str | None = None,
    max_bonus_rolls: int = 100,
    bonus_wait_ms: int = 1000,
    scrape_history: bool = False,
):
    """
    Main function to run the scraper.
    Args:
        picks (List[Pick], required): List of Pick objects to process.
        proxy (str | None, optional): Proxy URL to use. Defaults to None.
        use_google_oauth (bool, optional): Whether to use Google OAuth for login. Defaults
        user_data_dir (str | None, optional): Path to user data directory. Defaults to None.
        headless (bool, optional): Whether to run in headless mode. Defaults to False.
        skip_claim (bool, optional): Whether to skip the claim step. Defaults to False.
        play_keno (bool, optional): Whether to play keno after claiming. Defaults to False.
        summarize (bool, optional): Whether to summarize the results. Defaults to False.
        enable_screenshots (bool, optional): Enable screenshots. Defaults to False.

    Returns:
        None
    """
    # If scraping history with a user-data-dir, prepare profile (copy if locked)
    temp_profile_dir: str | None = None
    if scrape_history and user_data_dir:
        user_data_dir, is_temp = prepare_user_data_dir(user_data_dir)
        if is_temp:
            temp_profile_dir = user_data_dir

    finished_picks: List[Pick] = []
    tries: Dict[str, int] = {x.url: 0 for x in picks}
    while len(picks) > 0:
        pick = picks.pop(0)
        tries[pick.url] += 1
        log.info("Processing pick: %s (%d/%d)", pick.url, tries[pick.url], 3)
        if tries[pick.url] > 3:
            log.error("Exceeded maximum retries for %s, skipping.", pick.url)
            continue
        # Choose login method
        if use_google_oauth:
            login_page, claim_already_attempted = google_oauth_login_page_make()
        else:
            username, password, _ = get_credentials(pick.url)
            login_page, claim_already_attempted = login_page_make(
                username,
                password,
                currency=pick.currency,
                enable_screenshots=enable_screenshots,
            )

        with safe_stealthy_session(
            proxy=proxy,
            headless=headless,
            humanize=True,
            solve_cloudflare=True,
            google_search=False,
            user_data_dir=user_data_dir or "",
        ) as session:
            try:
                login_response: Response = session.fetch(
                    f"{pick.url}login.php",
                    page_action=login_page,
                    wait=3000,
                )

                logged_in = check_logged_in(login_response)

                # If login response was a redirect (302), the Response body
                # won't contain logout links. Fetch the main page to verify.
                if not logged_in:
                    log.info(
                        "Login check failed on initial response (status %s), "
                        "verifying via main page...",
                        login_response.status,
                    )
                    verify_response: Response = session.fetch(
                        pick.url,
                        wait=2000,
                        solve_cloudflare=False,
                    )
                    logged_in = check_logged_in(verify_response)

                if logged_in:
                    finished_picks.append(pick)
                    log.info("Logged in to %s successfully", pick.url)
                else:
                    log.error("Failed to log in to %s", pick.url)
                    picks.append(
                        pick
                    )  # Re-add to the end of the list to try again later
                    continue

                log.debug("%s", pick)

                if scrape_history:
                    log.info("Scraping bet history for %s...", pick.currency)
                    history_action, history_entries = make_scrape_bet_history_action(
                        currency=pick.currency,
                    )
                    try:
                        # Navigate to game_history.php where localforage JS is loaded
                        session.fetch(
                            f"{pick.url}game_history.php",
                            page_action=history_action,
                            network_idle=True,
                            wait=3000,
                            timeout=60000,
                            solve_cloudflare=False,
                        )
                    except Exception:
                        log.exception("[%s] Error extracting bet history", pick.currency)
                    if history_entries:
                        save_bet_history_csv(history_entries, pick.currency)
                        log.info("[%s] Total: %d entries exported", pick.currency, len(history_entries))
                    else:
                        log.warning(
                            "No bet history found for %s. "
                            "Bet history is stored in your browser's local storage. "
                            "Use --user-data-dir to point to your browser profile.",
                            pick.url,
                        )
                    continue

                if skip_claim:
                    log.info("Skipping claim as per --skip-claim")
                    continue

                if claim_already_attempted():
                    log.info(
                        "Skipping claim - already attempted during login (user was already logged in)"
                    )
                    continue

                faucet = make_claim_faucet(
                    button_selector,
                    currency=pick.currency,
                    enable_screenshots=enable_screenshots,
                )
                _: Response = session.fetch(
                    f"{pick.url}faucet.php",
                    page_action=faucet,
                    wait=2000,
                    timeout=10000,
                )
                log.info("after claim attempt for %s", pick.url)

                # Optionally run bonus rolls if requested
                if do_bonus_rolls:
                    bonus_action: Optional[Callable[[Page], int]] = None
                    if bonus_tab_selector and bonus_roll_selector:
                        bonus_action = make_bonus_rolls_faucet(
                            tab_selector=bonus_tab_selector,
                            roll_selector=bonus_roll_selector,
                            currency=pick.currency,
                            enable_screenshots=enable_screenshots,
                            max_rolls=max_bonus_rolls,
                            wait_between_ms=bonus_wait_ms,
                        )
                    else:
                        bonus_action = make_bonus_rolls_faucet(
                            currency=pick.currency,
                            enable_screenshots=enable_screenshots,
                            max_rolls=max_bonus_rolls,
                            wait_between_ms=bonus_wait_ms,
                        )
                    if bonus_action:
                        try:
                            _: Response = session.fetch(
                                f"{pick.url}faucet.php",
                                page_action=bonus_action,
                                solve_cloudflare=False,
                                wait=2000,
                            )
                            # timeout=max(10000, max_bonus_rolls * (bonus_wait_ms + 50)),
                            log.info("Completed bonus rolls for %s", pick.url)
                        except Exception:
                            log.exception("Error running bonus rolls for %s", pick.url)
                    else:
                        log.info("No bonus selectors available; skipping bonus rolls for %s", pick.url)

                # Check if Response object has a page attribute
                if play_keno:
                    log.info("About to play keno on %s", pick.url)
                    _: Response = session.fetch(
                        f"{pick.url}keno.php",
                        page_action=play_keno_func,
                        timeout=0,
                        solve_cloudflare=False,
                    )

                log.info("Finished processing %s", pick.url)

            except Exception as e:
                # Include full traceback to make Playwright/Scrapling failures debuggable
                log.exception("Error fetching %s: %s", pick.url, e)
                continue

    if summarize:
        summarize_picks(finished_picks)

    save_picks(finished_picks)

    # Clean up temp profile copy if we made one
    if temp_profile_dir:
        import shutil
        shutil.rmtree(temp_profile_dir, ignore_errors=True)
        log.info("Cleaned up temp profile copy: %s", temp_profile_dir)


def filter_picks(all_picks: List[Pick], only: List[str], skip: List[str]) -> List[Pick]:
    """
    Filter picks based on 'only' and 'skip' lists.
    Args:
        all_picks (List[Pick]): List of all Pick objects.
        only (List[str]): List of currency codes to run only.
        skip (List[str]): List of currency codes to skip.
    Returns:
        List[Pick]: Filtered list of Pick objects to run.
    """
    if only:
        to_run = [pick for pick in all_picks if pick.currency in only]
    elif skip:
        to_run = [pick for pick in all_picks if pick.currency not in skip]
    else:
        to_run = all_picks

    if not to_run:
        log.warning("No picks to run after applying --only/--skip filters.")
    return to_run


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--proxy", help="proxy url to use", default=None)
    parser.add_argument(
        "--user-data-dir",
        help="path to Firefox profile directory (required for --scrape-history). "
        "Linux: ~/.mozilla/firefox/*.default-release, "
        "Windows: %%APPDATA%%\\Mozilla\\Firefox\\Profiles\\*.default-release",
        default=None,
    )
    parser.add_argument("--headless", help="run in headless mode", action="store_true")
    parser.add_argument(
        "--google-oauth",
        action="store_true",
        help="use Google OAuth login instead of username/password",
    )
    parser.add_argument(
        "--skip-claim",
        action="store_true",
        help="skip claiming the faucet (just login and get balance)",
    )
    parser.add_argument(
        "--summarize", help="summarize the results", action="store_true"
    )
    parser.add_argument(
        "--play-keno",
        help="play keno game after claiming the faucet",
        action="store_true",
    )
    parser.add_argument(
        "--enable-screenshots",
        help="enable before/after screenshots for page actions",
        action="store_true",
    )
    # Bonus rolls options
    parser.add_argument(
        "--do-bonus-rolls",
        help="Navigate to the bonus faucet tab and perform repeated bonus rolls",
        action="store_true",
    )
    parser.add_argument(
        "--bonus-tab-selector",
        help="CSS selector to open the bonus tab (required when --do-bonus-rolls is set)",
        default=None,
    )
    parser.add_argument(
        "--bonus-roll-selector",
        help="CSS selector for the bonus roll button (required when --do-bonus-rolls is set)",
        default=None,
    )
    parser.add_argument(
        "--max-bonus-rolls",
        help="Maximum number of bonus rolls to attempt (default: 100)",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--bonus-wait-ms",
        help="Mean wait time in ms between bonus rolls (default: 1000)",
        type=int,
        default=1000,
    )
    parser.add_argument(
        "--scrape-history",
        help="Scrape full bet history from game_history.php and save to CSV (skips claim/keno/bonus)",
        action="store_true",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--skip", help="List of picks by currency to skip", nargs="+", default=[]
    )
    group.add_argument(
        "--only", help="List of picks by currency to run only", nargs="+", default=[]
    )

    args = parser.parse_args()

    load_env_file()

    all_picks = load_picks()
    if not all_picks:
        all_picks = default_picks()
    picks_to_run: List[Pick] = filter_picks(all_picks, args.only, args.skip)
    PICKS = {x.currency: x for x in picks_to_run}

    main(
        picks_to_run,
        args.proxy,
        args.google_oauth,
        args.user_data_dir,
        args.headless,
        args.skip_claim,
        args.play_keno,
        args.summarize,
        args.enable_screenshots,
        args.do_bonus_rolls,
        args.bonus_tab_selector,
        args.bonus_roll_selector,
        args.max_bonus_rolls,
        args.bonus_wait_ms,
        args.scrape_history,
    )
