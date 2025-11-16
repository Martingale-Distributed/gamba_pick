"""
Parameterized Casino Template
==============================

This template demonstrates how to create a casino automation script using the
parameterized configuration system. Instead of manually replacing placeholders,
you simply define a CasinoConfig object with your casino's specific parameters.

Usage:
------
1. Copy this file: cp casino_template_parameterized.py mycasino.py
2. Update the config object with your casino's selectors
3. Run: python mycasino.py

The factory function handles all the complexity for you!

Environment Variables Required:
-------------------------------
Set these before running:
- {SITE_PREFIX}_USERNAME: Your casino username
- {SITE_PREFIX}_PASSWORD: Your casino password
- {SITE_PREFIX}_2FA: Your 2FA TOTP secret (if required)
"""

from playwright.sync_api import Page
from typing import Dict, Optional
import re

from casino import (
    # Configuration classes
    CasinoConfig,
    LoginConfig,
    MTBClaimConfig,
    CurrencyDisplayConfig,
    Currency,
    # Factory function
    make_casino_automation,
    # Utilities
    get_arg_parser,
    log,
)

# ============================================================================
# STEP 1: Define Your Casino Configuration
# ============================================================================

# Configure login selectors
login_config = LoginConfig(
    username_selector='input[name="PLACEHOLDER_USERNAME"]',
    password_selector='input[name="PLACEHOLDER_PASSWORD"]',
    login_submit_selector='button[type="submit"]',
    totp_code_selector='input[name="PLACEHOLDER_2FA"]',  # Optional, set to None if no 2FA
    totp_submit_selector='button[type="submit"]',  # Often same as login_submit
    # Optional: Add pre-login callback if needed
    # pre_login_callback=lambda page: page.click('div[id="tab-login"]'),
    # Optional: Add post-login callback if needed
    # post_login_callback=None,
)

# Configure currency display
currency_display_config = CurrencyDisplayConfig(
    currencies=[
        Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=[
                '[data-testid="PLACEHOLDER_SC"]',
                '.PLACEHOLDER_sweeps_class',
            ],
        ),
        Currency(
            name="Gold Coins",
            code="GC",
            selectors=[
                '[data-testid="PLACEHOLDER_GC"]',
                '.PLACEHOLDER_gold_class',
            ],
        ),
    ],
    # Set these if you need to click to open/switch currency display
    currency_toggle_dropdown_selector='button[data-testid="PLACEHOLDER_CURRENCY_TOGGLE"]',
    currency_toggle_switch_selector=None,  # Or selector to switch between currencies
)

# Configure bonus claiming - Choose ONE pattern:

# Option A: Modal-Tab-Button (MTB) pattern - like stake.us
claim_config = MTBClaimConfig(
    modal_selector='button[data-testid="PLACEHOLDER_WALLET"]',
    tab_selector='button[data-testid="PLACEHOLDER_DAILY_TAB"]',
    btn_selector='button.PLACEHOLDER_CLAIM_CLASS',
    close_btn_selector='button[data-testid="PLACEHOLDER_CLOSE"]',
)
claim_pattern = "mtb"

# Option B: Generic Accept/Close pattern - like luckybird.io
# claim_config = GenericClaimConfig(
#     main_enabled_selector="section.PLACEHOLDER_MODAL button:enabled",
#     modal_selector="section.PLACEHOLDER_MODAL",
#     close_modal_selector="section.PLACEHOLDER_MODAL .PLACEHOLDER_CLOSE",
# )
# claim_pattern = "generic"

# ============================================================================
# STEP 2 (Optional): Define Custom Balance Parser
# ============================================================================
# Only needed if the default parser doesn't work for your casino


def custom_balance_parser(page: Page) -> Dict[str, Optional[float]]:
    """
    Custom balance parser for casinos with unique DOM structure.

    Return a dict with 'gold_coins' and 'sweeps_coins' keys.
    """
    balances = {"gold_coins": None, "sweeps_coins": None}
    number_pattern = re.compile(r"[\d,]+\.?\d*")

    try:
        # Example: Parse gold coins
        gc_element = page.locator('.PLACEHOLDER_gold_selector').first
        if gc_element.count() > 0:
            text = gc_element.text_content()
            if text:
                match = number_pattern.search(text)
                if match:
                    value_str = match.group().replace(",", "")
                    balances["gold_coins"] = float(value_str)
                    log.info("Found Gold Coins: %s", balances["gold_coins"])

        # Example: Parse sweeps coins
        sc_element = page.locator('.PLACEHOLDER_sc_selector').first
        if sc_element.count() > 0:
            text = sc_element.text_content()
            if text:
                match = number_pattern.search(text)
                if match:
                    value_str = match.group().replace(",", "")
                    balances["sweeps_coins"] = float(value_str)
                    log.info("Found Sweeps Coins: %s", balances["sweeps_coins"])

    except Exception as e:
        log.error("Error parsing balances: %s", str(e))

    return balances


# ============================================================================
# STEP 3 (Optional): Define Additional Actions
# ============================================================================
# These run after login, balance check, and bonus claim


def additional_action_example(page: Page) -> None:
    """Example additional action - navigate to a specific game."""
    page.goto("https://PLACEHOLDER.com/games/GAME_NAME")
    log.info("Navigated to specific game")


# ============================================================================
# STEP 4: Create Complete Casino Configuration
# ============================================================================

config = CasinoConfig(
    # Basic info
    name="PLACEHOLDER Casino Name",  # e.g., "Stake.us"
    url="https://PLACEHOLDER.com",
    login_url="https://PLACEHOLDER.com/?modal=login",  # Can be same as url
    description="PLACEHOLDER Casino Automation Script",

    # Configuration objects
    login=login_config,
    currency_display=currency_display_config,
    claim_config=claim_config,
    claim_pattern=claim_pattern,

    # Optional: Use custom balance parser
    # custom_balance_parser=custom_balance_parser,
    custom_balance_parser=None,  # Use default parser

    # Optional: Additional actions to run after standard flow
    # additional_actions=[additional_action_example],
    additional_actions=[],

    # Optional: Timing configuration
    page_wait_timeout=5000,  # ms to wait after page loads
    fetch_timeout=60000,  # ms for total page load timeout

    # Optional: Does this casino require 2FA?
    requires_2fa=False,  # Set to True if 2FA is mandatory
)

# ============================================================================
# STEP 5: Generate the Main Function
# ============================================================================

# The factory function creates a complete main() function for you!
main = make_casino_automation(config)

# ============================================================================
# STEP 6: CLI Entry Point
# ============================================================================

if __name__ == "__main__":
    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    # Call the generated main function with CLI arguments
    main(**vars(args))


# ============================================================================
# That's it! The configuration system handles everything else:
# - Creating login actions
# - Parsing account state
# - Claiming bonuses
# - Error handling
# - Logging
# - Browser session management
# ============================================================================

"""
Quick Reference - Common Patterns
==================================

1. DROPDOWN CURRENCY TOGGLE (like Stake.us):
   currency_toggle_dropdown_selector='button[data-testid="coin-toggle"]'
   currency_toggle_switch_selector=None

2. DIRECT DISPLAY (like LuckyBird.io):
   currency_toggle_dropdown_selector=None
   currency_toggle_switch_selector=None

3. PRE-LOGIN CALLBACK (click login tab):
   pre_login_callback=lambda page: page.click('div[id="tab-login"]')

4. GOOGLE ONE TAP POPUP:
   from casino import make_handle_google_one_tap_popup, close_selectors
   pre_login_callback=make_handle_google_one_tap_popup(close_selectors)

5. MTB CLAIMING (Wallet → Daily Bonus → Claim):
   claim_config = MTBClaimConfig(
       modal_selector='button[data-testid="wallet"]',
       tab_selector='button[data-testid="dailyBonus"]',
       btn_selector='button.claim-btn',
       close_btn_selector='button[data-testid="modal-close"]',
   )
   claim_pattern = "mtb"

6. GENERIC CLAIMING (Auto-popup with claim button):
   claim_config = GenericClaimConfig(
       main_enabled_selector="section.modal button:enabled",
       modal_selector="section.modal",
       close_modal_selector="section.modal .close",
   )
   claim_pattern = "generic"

7. CURRENCY WITH TOGGLE (need to click to see other currency):
   Currency(
       name="Sweeps Coins",
       code="SC",
       selectors=['.sweeps_color .amount'],
       activate_selector='.sweeps_color',  # Click this to activate
   )

8. ADDITIONAL ACTIONS (run after standard flow):
   def my_action(page: Page) -> None:
       page.click('.some-button')

   config = CasinoConfig(
       ...,
       additional_actions=[my_action],
   )
"""
