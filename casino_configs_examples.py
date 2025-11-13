"""
Casino Configuration Examples
==============================

This file demonstrates how to use the parameterized casino configuration system
with real-world examples from Stake.us and LuckyBird.io.

These examples show:
1. How to convert existing implementations to the config system
2. Different patterns (MTB vs Generic claiming)
3. Different currency display patterns
4. Custom balance parsers
5. Pre-login callbacks

Usage:
------
You can run these examples directly or use them as reference for your own configs.

Example:
    python casino_configs_examples.py stake
    python casino_configs_examples.py luckybird
"""

from playwright.sync_api import Page
from typing import Dict, Optional
import re
import sys

from casino import (
    # Configuration classes
    CasinoConfig,
    LoginConfig,
    MTBClaimConfig,
    GenericClaimConfig,
    CurrencyDisplayConfig,
    Currency,
    # Factory function
    make_casino_automation,
    # Utilities
    get_arg_parser,
    gaussian_random_delay,
    log,
)

# ============================================================================
# Example 1: Stake.us Configuration (MTB Pattern)
# ============================================================================


def create_stake_us_config() -> CasinoConfig:
    """Create configuration for Stake.us casino.

    This casino uses:
    - Modal-Tab-Button (MTB) claiming pattern
    - Dropdown currency toggle
    - Standard login with optional 2FA
    """
    return CasinoConfig(
        name="Stake.us",
        url="https://stake.us",
        login_url="https://stake.us/?tab=login&modal=auth",
        description="Stake.us Casino Automation",
        login=LoginConfig(
            username_selector='input[name="emailOrName"]',
            password_selector='input[name="password"]',
            login_submit_selector='button[type="submit"]',
            totp_code_selector='input[name="code"]',
            totp_submit_selector='button[type="submit"]',
            # No pre-login callback needed for Stake.us
            pre_login_callback=None,
            post_login_callback=None,
        ),
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=['[data-testid="coin-toggle-currency-sweeps"]'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=['[data-testid="coin-toggle-currency-gold"]'],
                ),
            ],
            currency_toggle_dropdown_selector='button[data-testid="coin-toggle"]',
            currency_toggle_switch_selector=None,
        ),
        claim_config=MTBClaimConfig(
            modal_selector='button[data-testid="wallet"], button[data-analytics="global-navbar-wallet-button"]',
            tab_selector='button[data-testid="dailyBonus"]',
            btn_selector="button.justify-center:nth-child(4)",
            close_btn_selector='button[data-testid="modal-close"]',
        ),
        claim_pattern="mtb",
        custom_balance_parser=None,  # Use default parser
        additional_actions=[],
        page_wait_timeout=5000,
        fetch_timeout=60000,
        requires_2fa=False,  # 2FA is optional
    )


# ============================================================================
# Example 2: LuckyBird.io Configuration (Generic Pattern)
# ============================================================================


def luckybird_custom_balance_parser(page: Page) -> Dict[str, Optional[float]]:
    """Custom balance parser for LuckyBird.io.

    LuckyBird requires clicking a currency switcher to toggle between
    GC and SC displays.
    """
    balances = {"gold_coins": None, "sweeps_coins": None}
    number_pattern = re.compile(r"[\d,]+\.?\d*")

    # Currency switcher selector
    currency_switcher_selector = ".currency-disabled"
    active_amount_selector = ".currency-active .amount"

    try:
        # First, get the currently active currency amount
        active_element = page.locator(active_amount_selector).first
        if active_element.count() > 0:
            text = active_element.text_content()
            if text:
                match = number_pattern.search(text)
                if match:
                    value_str = match.group().replace(",", "")
                    first_value = float(value_str)

                    # Determine which currency is currently active
                    gold_active = page.locator(".gold_color.currency-active").count() > 0

                    if gold_active:
                        balances["gold_coins"] = first_value
                        log.info("Found Gold Coins balance: %s", balances["gold_coins"])
                    else:
                        balances["sweeps_coins"] = first_value
                        log.info("Found Sweeps Coins balance: %s", balances["sweeps_coins"])

        # Now click to toggle to the other currency
        try:
            switcher = page.locator(currency_switcher_selector).first
            switcher.click(delay=gaussian_random_delay(), timeout=3000)
            page.wait_for_timeout(500)

            # Get the newly active currency amount
            active_element = page.locator(active_amount_selector).first
            if active_element.count() > 0:
                text = active_element.text_content()
                if text:
                    match = number_pattern.search(text)
                    if match:
                        value_str = match.group().replace(",", "")
                        second_value = float(value_str)

                        # Determine which currency is now active
                        gold_active = page.locator(".gold_color.currency-active").count() > 0

                        if gold_active:
                            balances["gold_coins"] = second_value
                            log.info("Found Gold Coins balance: %s", balances["gold_coins"])
                        else:
                            balances["sweeps_coins"] = second_value
                            log.info("Found Sweeps Coins balance: %s", balances["sweeps_coins"])

            # Click again to restore original currency display
            switcher.click(delay=gaussian_random_delay(), timeout=3000)

        except Exception as e:
            log.warning("Error toggling currency display: %s", str(e))

    except Exception as e:
        log.error("Error parsing coin balances: %s", str(e))

    return balances


def create_luckybird_config() -> CasinoConfig:
    """Create configuration for LuckyBird.io casino.

    This casino uses:
    - Generic Accept/Close modal claiming pattern
    - Direct currency display (both visible at once)
    - Pre-login callback to click login tab
    - Custom balance parser for currency switching
    - Required 2FA
    """
    return CasinoConfig(
        name="LuckyBird.io",
        url="https://luckybird.io/",
        login_url="https://luckybird.io/",
        description="LuckyBird.io Daily Bonus Claimer",
        login=LoginConfig(
            username_selector=(
                "form.el-form:nth-child(2) > div:nth-child(1) > "
                "div:nth-child(2) > div:nth-child(1) > input:nth-child(1)"
            ),
            password_selector=(
                "form.el-form:nth-child(2) > div:nth-child(2) > "
                "div:nth-child(2) > div:nth-child(1) > input:nth-child(1)"
            ),
            login_submit_selector="button.tw-mt-10",
            totp_code_selector=".loginTwoFactor_input > input:nth-child(1)",
            totp_submit_selector=".loginTwoFactor_button",
            # LuckyBird requires clicking the login tab before filling the form
            pre_login_callback=lambda page: page.click(
                'div[id="tab-login"]', delay=gaussian_random_delay(), timeout=10000
            ),
            post_login_callback=None,
        ),
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=[".sweeps_color .amount", ".sc_color .amount", "[class*='sweeps'] .amount"],
                    activate_selector=".sweeps_color",
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=[".gold_color .amount"],
                    activate_selector=".gold_color",
                ),
            ],
            # LuckyBird shows both currencies at once
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),
        claim_config=GenericClaimConfig(
            main_enabled_selector="section.dailyBonus_page button.el-button--primary:enabled",
            modal_selector="section.dailyBonus_page",
            close_modal_selector="section.dailyBonus_page .commonAlert_close",
        ),
        claim_pattern="generic",
        # Use custom balance parser for LuckyBird's unique currency toggle
        custom_balance_parser=luckybird_custom_balance_parser,
        additional_actions=[],
        page_wait_timeout=5000,
        fetch_timeout=60000,
        requires_2fa=True,  # LuckyBird requires 2FA
    )


# ============================================================================
# Example 3: Generic Casino Template
# ============================================================================


def create_generic_casino_config() -> CasinoConfig:
    """Create a generic casino configuration template.

    Use this as a starting point for new casinos.
    """
    return CasinoConfig(
        name="Generic Casino",
        url="https://example.com",
        login_url="https://example.com/login",
        description="Generic Casino Automation Template",
        login=LoginConfig(
            username_selector='input[name="username"]',
            password_selector='input[name="password"]',
            login_submit_selector='button[type="submit"]',
            totp_code_selector='input[name="code"]',
            totp_submit_selector='button[type="submit"]',
        ),
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=[".sc-balance", '[data-currency="SC"]'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=[".gc-balance", '[data-currency="GC"]'],
                ),
            ],
            currency_toggle_dropdown_selector='button[data-testid="currency-toggle"]',
            currency_toggle_switch_selector=None,
        ),
        claim_config=MTBClaimConfig(
            modal_selector='button[data-testid="wallet"]',
            tab_selector='button[data-testid="daily-bonus"]',
            btn_selector='button.claim-button',
            close_btn_selector='button[data-testid="close"]',
        ),
        claim_pattern="mtb",
        custom_balance_parser=None,
        additional_actions=[],
        page_wait_timeout=5000,
        fetch_timeout=60000,
        requires_2fa=False,
    )


# ============================================================================
# Configuration Registry
# ============================================================================

CASINO_CONFIGS = {
    "stake": create_stake_us_config,
    "luckybird": create_luckybird_config,
    "generic": create_generic_casino_config,
}

# ============================================================================
# CLI Entry Point
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2 or sys.argv[1] not in CASINO_CONFIGS:
        print("Usage: python casino_configs_examples.py <casino>")
        print(f"Available casinos: {', '.join(CASINO_CONFIGS.keys())}")
        sys.exit(1)

    # Get the casino name from command line
    casino_name = sys.argv[1]

    # Create the config
    config = CASINO_CONFIGS[casino_name]()

    # Generate the main function
    main = make_casino_automation(config)

    # Parse remaining CLI arguments
    parser = get_arg_parser(description=config.description)
    # Remove the casino name from args before parsing
    args = parser.parse_args(sys.argv[2:])

    # Run the automation
    main(**vars(args))


# ============================================================================
# Usage Examples
# ============================================================================

"""
To use these configurations:

1. Run Stake.us automation:
   python casino_configs_examples.py stake
   python casino_configs_examples.py stake --headless
   python casino_configs_examples.py stake --skip-claim

2. Run LuckyBird.io automation:
   python casino_configs_examples.py luckybird
   python casino_configs_examples.py luckybird --headless

3. Use as a reference for your own configs:
   from casino_configs_examples import create_stake_us_config

   # Customize the config
   config = create_stake_us_config()
   config.name = "My Stake Clone"
   config.url = "https://mystakeclone.com"

   # Generate and run
   main = make_casino_automation(config)
   main(headless=True)

4. Create a new config for your casino:
   def create_mycasino_config() -> CasinoConfig:
       return CasinoConfig(
           name="MyCasino",
           url="https://mycasino.com",
           ...
       )

   config = create_mycasino_config()
   main = make_casino_automation(config)
   main()
"""
