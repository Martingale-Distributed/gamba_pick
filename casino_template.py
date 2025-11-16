"""
Casino Template Implementation
===============================

This template provides a complete structure for implementing casino automation scripts.
It is based on the patterns found in stake_us.py and luckybird.py implementations.

Usage:
------
1. Copy this file and rename it to your casino name (e.g., mycasino.py)
2. Replace all PLACEHOLDER values with your casino-specific selectors
3. Configure the currency display settings
4. Choose the appropriate claiming pattern (Modal-Tab-Button or Generic Accept/Close)
5. Update the URL and login URL
6. Test your implementation

Environment Variables Required:
-------------------------------
Set these environment variables before running:
- {SITE_PREFIX}_USERNAME: Your casino username
- {SITE_PREFIX}_PASSWORD: Your casino password
- {SITE_PREFIX}_2FA: Your 2FA TOTP secret (if applicable)

Where {SITE_PREFIX} is derived from your URL (e.g., STAKE for stake.us)
"""

from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Callable, Optional, List, Dict
import sys
import os
import pyotp
import re

# Scrapling and Playwright imports
from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import StealthySession
from playwright.sync_api import Page
from playwright._impl._errors import TimeoutError, TargetClosedError

# Casino module imports
from casino import (
    get_credentials,
    gaussian_random_delay,
    get_arg_parser,
    wait_for_load_all_safe,
    log,
    make_handle_google_one_tap_popup,
    make_login_action_factory,
    make_modal_tab_button,
    make_generic_accept_or_close_modals,
    make_get_casino_account_state,
    CurrencyDisplayConfig,
    Currency,
    CasinoAccountState,
)

# ============================================================================
# STEP 1: Configure URLs
# ============================================================================
url = "https://PLACEHOLDER.com"  # Main casino URL
login_url = "https://PLACEHOLDER.com/?modal=login"  # Login page URL (may be same as main URL)

# ============================================================================
# STEP 2: Configure Login Selectors
# ============================================================================
# These selectors are used to locate login form elements
username_selector = 'input[name="PLACEHOLDER_USERNAME"]'  # Username/email input field
password_selector = 'input[name="PLACEHOLDER_PASSWORD"]'  # Password input field
totp_code_selector = 'input[name="PLACEHOLDER_2FA_CODE"]'  # 2FA/TOTP code input field (optional)
login_submit_selector = 'button[type="submit"]'  # Login button
totp_submit_selector = 'button[type="submit"]'  # 2FA submit button (can be same as login_submit)

# ============================================================================
# STEP 3: Configure Currency Display Selectors
# ============================================================================
# Option A: Dropdown-based currency toggle (like stake.us)
currency_toggle_dropdown_selector = 'button[data-testid="PLACEHOLDER_CURRENCY_TOGGLE"]'
currency_toggle_switch_selector = None  # Set if you need to click between currencies in dropdown

# Option B: Direct display (like luckybird.io) - set dropdown to None
# currency_toggle_dropdown_selector = None
# currency_toggle_switch_selector = None

# Currency amount selectors
sweeps_coins_selectors = [
    '[data-testid="PLACEHOLDER_SC"]',
    '.PLACEHOLDER_sweeps_class',
    # Add multiple selectors as fallbacks
]

gold_coins_selectors = [
    '[data-testid="PLACEHOLDER_GC"]',
    '.PLACEHOLDER_gold_class',
    # Add multiple selectors as fallbacks
]

# ============================================================================
# STEP 4: Configure Bonus Claiming Selectors
# ============================================================================
# Pattern 1: Modal-Tab-Button (MTB) - like stake.us
# Use this pattern when claiming involves: Open modal -> Click tab -> Click claim button
wallet_btn_selector = 'button[data-testid="PLACEHOLDER_WALLET"]'  # Button to open wallet/modal
daily_bonus_tab_selector = 'button[data-testid="PLACEHOLDER_DAILY_TAB"]'  # Tab inside modal
claim_btn_selector = 'button.PLACEHOLDER_CLAIM_CLASS'  # Claim button
close_btn_selector = 'button[data-testid="PLACEHOLDER_CLOSE"]'  # Modal close button

# Pattern 2: Generic Accept/Close Modals - like luckybird.io
# Use this pattern when the site shows automatic popups with claim buttons
main_enabled_selector = "section.PLACEHOLDER_MODAL button.PLACEHOLDER_ENABLED:enabled"
modal_selector = "section.PLACEHOLDER_MODAL"
close_modal_selector = "section.PLACEHOLDER_MODAL .PLACEHOLDER_CLOSE"

# ============================================================================
# STEP 5: Configure Google One Tap Popup (if needed)
# ============================================================================
close_selectors = [
    "#close",
    "div#close",
    "[aria-label='Close']",
    "button[aria-label='Close']",
    ".close",
]

# ============================================================================
# STEP 6: Create Currency Display Configuration
# ============================================================================
# This configuration tells the system how to read your casino's currency display
currency_display_config = CurrencyDisplayConfig(
    currencies=[
        # Define each currency type the casino supports
        Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=sweeps_coins_selectors,
            # Optional: selector to check if this currency is active
            is_active_selector=None,
            # Optional: selector to click to activate this currency view
            activate_selector=None,
        ),
        Currency(
            name="Gold Coins",
            code="GC",
            selectors=gold_coins_selectors,
            is_active_selector=None,
            activate_selector=None,
        ),
    ],
    currency_toggle_dropdown_selector=currency_toggle_dropdown_selector,
    currency_toggle_switch_selector=currency_toggle_switch_selector,
)

# ============================================================================
# STEP 7: Create Login Action Factory
# ============================================================================
# This factory creates the login function with your specific selectors
login_action_factory: Callable[[str, str, Optional[str]], Callable[[Page], None]] = make_login_action_factory(
    username_selector=username_selector,
    password_selector=password_selector,
    login_submit_selector=login_submit_selector,
    totp_code_selector=totp_code_selector,
    totp_submit_selector=totp_submit_selector,
    # Optional: Add pre-login callback if needed (e.g., to click a "Login" tab first)
    pre_login_form_callback=None,  # Example: lambda page: page.click('div[id="tab-login"]')
    # Optional: Add post-login callback if needed
    post_login_form_callback=None,
)

# ============================================================================
# STEP 8: Create Account State Parser
# ============================================================================
# This function will read the currency balances from the page
get_casino_account_state = make_get_casino_account_state(currency_display_config)

# ============================================================================
# STEP 9: Create Bonus Claiming Action
# ============================================================================
# Choose ONE of the following patterns based on your casino's interface:

# Pattern 1: Modal-Tab-Button (MTB) - Uncomment if your casino uses this pattern
claim_bonus_action = make_modal_tab_button(
    modal_selector=wallet_btn_selector,
    tab_selector=daily_bonus_tab_selector,
    btn_selector=claim_btn_selector,
    close_btn_selector=close_btn_selector,
)

# Pattern 2: Generic Accept/Close Modals - Uncomment if your casino uses this pattern
# claim_bonus_action = make_generic_accept_or_close_modals(
#     main_enabled_selector=main_enabled_selector,
#     modal_selector=modal_selector,
#     close_modal_selector=close_modal_selector,
# )

# ============================================================================
# STEP 10 (Optional): Custom Account State Parser
# ============================================================================
# If the default parser doesn't work, create a custom one like luckybird.io:


def parse_coin_balances_custom(page: Page) -> Dict[str, Optional[float]]:
    """
    Custom balance parser for casinos with unique currency display logic.

    This is an example based on luckybird.io's implementation.
    Modify this function to match your casino's specific DOM structure.

    Args:
        page: The Playwright Page object

    Returns:
        Dict with 'gold_coins' and 'sweeps_coins' keys
    """
    balances = {"gold_coins": None, "sweeps_coins": None}

    # Pattern to extract numeric values (handles formats like "1,234.56" or "1234.56")
    number_pattern = re.compile(r"[\d,]+\.?\d*")

    try:
        # Example: Read gold coins
        gold_element = page.locator(gold_coins_selectors[0]).first
        if gold_element.count() > 0:
            text = gold_element.text_content()
            if text:
                match = number_pattern.search(text)
                if match:
                    value_str = match.group().replace(",", "")
                    balances["gold_coins"] = float(value_str)
                    log.info("Found Gold Coins balance: %s", balances["gold_coins"])

        # Example: Read sweeps coins
        sweeps_element = page.locator(sweeps_coins_selectors[0]).first
        if sweeps_element.count() > 0:
            text = sweeps_element.text_content()
            if text:
                match = number_pattern.search(text)
                if match:
                    value_str = match.group().replace(",", "")
                    balances["sweeps_coins"] = float(value_str)
                    log.info("Found Sweeps Coins balance: %s", balances["sweeps_coins"])

    except Exception as e:
        log.error("Error parsing coin balances: %s", str(e))

    return balances


# ============================================================================
# STEP 11: Main Casino Action Function
# ============================================================================
def main(
    headless: bool = False,
    google_oauth: bool = False,
    skip_claim: bool = False,
    proxy: Optional[str] = None,
    user_data_dir: Optional[str] = None,
):
    """
    Main function that orchestrates the casino automation.

    Args:
        headless: Run browser in headless mode (no GUI)
        google_oauth: Enable Google OAuth handling (if applicable)
        skip_claim: Skip claiming the daily bonus
        proxy: Proxy server to use (e.g., http://user:pass@host:port)
        user_data_dir: Path to user data directory for browser session
    """
    # Get credentials from environment variables
    username, password, totp_secret = get_credentials(url, twofa=bool(totp_code_selector))

    # Configure additional browser arguments
    additional_args = {}
    if user_data_dir is not None:
        additional_args["user_data_dir"] = user_data_dir

    # Create the login action with credentials
    login_action: Callable[[Page], None] = login_action_factory(username, password, totp_secret)

    def casino_action(page: Page) -> None:
        """
        The main page action that performs all casino operations.

        This function is called by StealthySession after the page loads.
        Add your custom logic here.
        """
        # Step 1: Perform login
        log.info("Starting login process...")
        login_action(page)
        wait_for_load_all_safe(page)
        log.info("Login completed successfully")

        # Step 2: Get account state (balances)
        log.info("Reading account balances...")

        # Option A: Use the default parser
        account_state: CasinoAccountState = get_casino_account_state(page)
        log.info("Account State: %s", account_state)

        # Option B: Use custom parser (uncomment if needed)
        # balances = parse_coin_balances_custom(page)
        # log.info("Coin Balances: %s", balances)

        # Step 3: Claim daily bonus (unless skipped)
        if not skip_claim:
            log.info("Attempting to claim daily bonus...")
            try:
                # If using MTB pattern:
                claim_bonus_action(page)

                # If using Generic Accept/Close pattern:
                # claimed = claim_bonus_action(page)
                # if claimed:
                #     log.info("Daily bonus claimed successfully")
                # else:
                #     log.info("No daily bonus available to claim")

            except Exception as e:
                log.error("Error during bonus claim: %s", str(e))
        else:
            log.info("Skipping daily bonus claim (--skip-claim flag set)")

        wait_for_load_all_safe(page)

        # Step 4: Add any additional actions here
        # Example: Navigate to a specific game, make bets, etc.
        # page.goto(f"{url}/games/GAME_NAME")
        # wait_for_load_all_safe(page)

        log.info("Casino action completed successfully")

    # Create a stealthy browser session and execute the casino action
    with StealthySession(
        proxy=proxy,
        headless=headless,
        humanize=True,  # Add human-like behaviors
        load_dom=True,  # Ensure DOM is fully loaded
        google_search=False,  # Disable Google search emulation
        additional_args=additional_args,
    ) as session:
        log.info(f"Fetching {login_url}...")
        _: Response = session.fetch(
            login_url,
            page_action=casino_action,
            wait=5000,  # Wait 5 seconds after page load
            timeout=60000,  # 60 second timeout
        )
        log.info("Session completed")


# ============================================================================
# STEP 12: Command-Line Interface
# ============================================================================
if __name__ == "__main__":
    # Create argument parser with standard options
    parser = get_arg_parser(description="PLACEHOLDER Casino Automation - Replace with your casino name")
    args = parser.parse_args()

    # Run the main function with parsed arguments
    main(**vars(args))
