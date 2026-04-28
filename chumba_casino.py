"""
Chumba Casino Automation Script
================================

This script automates login, daily bonus claiming, and balance checking for chumbacasino.com
using the parameterized CasinoConfig system.

Usage:
------
python chumba_casino.py [--headless] [--skip-claim] [--proxy URL]
"""

from playwright.sync_api import Page
from typing import Optional

from casino import (
    CasinoConfig,
    LoginConfig,
    MTBClaimConfig,
    CurrencyDisplayConfig,
    Currency,
    get_arg_parser,
    gaussian_random_delay,
    log,
)
from scrapling_ext import make_casino_automation

# ============================================================================
# Callbacks & Helpers
# ============================================================================

def click_login_button(page: Page) -> None:
    """Click the login button to open the login modal if needed."""
    try:
        # Try to find and click a login button to open the modal
        login_btn_selectors = [
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            'a:has-text("Log In")',
            '[data-testid="login-button"]',
            ".login-button",
        ]
        for selector in login_btn_selectors:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(delay=gaussian_random_delay(), timeout=5000)
                    page.wait_for_timeout(1000)
                    log.info("Clicked login button: %s", selector)
                    return
            except Exception:
                continue
        log.info("No login button found, login form may already be visible")
    except Exception as e:
        log.warning("Error clicking login button: %s", str(e))


def handle_popup_bonuses(page: Page) -> None:
    """Handle any popup bonuses that appear automatically after login."""
    accept_tokens = {"claim", "collect", "accept", "get", "yes", "okay"}
    
    try:
        # Check for bonus modals
        modal_selectors = [
            '[class*="bonus-modal"]',
            '[class*="daily-bonus"]',
            ".modal:visible",
            '[role="dialog"]:visible',
        ]

        for modal_selector in modal_selectors:
            modal = page.locator(modal_selector).first
            if modal.count() > 0 and modal.is_visible():
                log.info("Found bonus modal: %s", modal_selector)

                # Look for claim buttons
                claim_selectors = [
                    'button:has-text("Claim")',
                    'button:has-text("Collect")',
                    'button:has-text("Accept")',
                    'button[class*="claim"]:enabled',
                ]

                for btn_selector in claim_selectors:
                    try:
                        btn = modal.locator(btn_selector).first
                        if btn.count() > 0 and btn.is_visible():
                            btn_text = btn.text_content() or ""
                            if any(token in btn_text.lower() for token in accept_tokens):
                                btn.click(delay=gaussian_random_delay(), timeout=5000)
                                log.info("Claimed bonus with button: %s", btn_text)
                                page.wait_for_timeout(1000)
                                break
                    except Exception as e:
                        log.debug("Button click failed: %s", str(e))

                # Close modal if still open
                try:
                    close_selectors = [
                        'button[aria-label="Close"]',
                        'button[class*="close"]',
                        ".modal-close",
                        'button:has-text("X")',
                    ]
                    for close_sel in close_selectors:
                        close_btn = page.locator(close_sel).first
                        if close_btn.count() > 0 and close_btn.is_visible():
                            close_btn.click(delay=gaussian_random_delay(), timeout=3000)
                            break
                except Exception:
                    pass

    except Exception as e:
        log.debug("Error handling popup bonuses: %s", str(e))


# ============================================================================
# Casino Configuration
# ============================================================================

def create_chumba_config() -> CasinoConfig:
    return CasinoConfig(
        name="Chumba Casino",
        url="https://www.chumbacasino.com",
        login_url="https://www.chumbacasino.com",
        description="Chumba Casino Automation",
        
        login=LoginConfig(
            username_selector='input[type="email"], input[name="email"], input[placeholder*="email" i]',
            password_selector='input[type="password"], input[name="password"]',
            login_submit_selector='button[type="submit"], button:has-text("Log In"), button:has-text("Sign In")',
            totp_code_selector='input[name="code"], input[type="tel"][maxlength="6"], input[autocomplete="one-time-code"]',
            totp_submit_selector='button[type="submit"], button:has-text("Verify"), button:has-text("Submit")',
            pre_login_callback=click_login_button,
            post_login_callback=handle_popup_bonuses,
        ),
        
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=[
                        '[data-testid="sweeps-balance"]',
                        '[class*="sweeps"] [class*="balance"]',
                        '[class*="sc-balance"]',
                        ".sweeps-coins-balance",
                        '[class*="SweepsCoin"]',
                        'span:has-text("SC") + span',
                    ],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=[
                        '[data-testid="gold-balance"]',
                        '[class*="gold"] [class*="balance"]',
                        '[class*="gc-balance"]',
                        ".gold-coins-balance",
                        '[class*="GoldCoin"]',
                        'span:has-text("GC") + span',
                    ],
                ),
            ],
            currency_toggle_dropdown_selector='[data-testid="currency-toggle"], button[class*="currency"], .currency-switcher',
            currency_toggle_switch_selector=None,
        ),
        
        claim_config=MTBClaimConfig(
            modal_selector='button[data-testid="wallet"], button:has-text("Wallet"), [class*="wallet-button"]',
            tab_selector='button[data-testid="daily-bonus"], button:has-text("Daily"), [class*="daily-bonus"]',
            btn_selector='button:has-text("Claim"), button:has-text("Collect"), button[class*="claim"]:enabled',
            close_btn_selector='button[aria-label="Close"], button[data-testid="close"], .modal-close, [class*="close-button"]',
        ),
        claim_pattern="mtb",
        
        requires_2fa=False,
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


# ============================================================================
# Main Execution
# ============================================================================

if __name__ == "__main__":
    config = create_chumba_config()
    main = make_casino_automation(config)
    
    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()
    
    main(**vars(args))
