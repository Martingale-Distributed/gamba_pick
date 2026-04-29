"""
Pulsz Bingo Casino Automation Script
====================================

Automates login + daily claim on www.pulszbingo.com. Pulsz Bingo is the
sister site of Pulsz — same React + CSS-modules frontend, same operator,
**identical** ``data-test`` hooks for currency display
(``header-goldcoins-value`` / ``header-sweepstakes-value``).

Daily-claim mechanic differs from Pulsz, though: where Pulsz uses a
7-day streak modal with a "GET FREE COINS" CTA, Pulsz Bingo's lobby
ships a "Wheel of Winners" prize wheel inside ``[data-test="common-
modal"]``. The wheel canvas is wrapped in a div carrying
``[data-test="wheel-of-winners-daily-reward"]`` — clicking it spins
the wheel (~6-8 second animation). After the wheel settles, a
``GET MY COINS`` ``MuiButton`` appears to collect the prize.
A coin-store upsell auto-pops in the same modal slot afterward; the
standard ``button[aria-label="back button"]`` close icon dismisses it.

Auth path: Google OAuth (same as Pulsz). First-time setup:

  python pulszbingo.py --google-oauth --setup

Normal headless runs after setup:

  python pulszbingo.py --google-oauth [--headless]

Currency display (verified live in the authenticated lobby):

    [data-test="header-goldcoins-value"]    — Gold Coins
    [data-test="header-sweepstakes-value"]  — Sweeps Coins
"""

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    MTBClaimConfig,
    get_arg_parser,
)
from scrapling_ext import make_casino_automation


def create_pulszbingo_config() -> CasinoConfig:
    return CasinoConfig(
        name="PulszBingo",
        url="https://www.pulszbingo.com",
        login_url="https://www.pulszbingo.com/login",
        description="Pulsz Bingo Casino Automation",

        login=LoginConfig(
            # Form-login placeholders — only used when ``--google-oauth``
            # is OFF. Same generic shape as ``pulsz.py``.
            username_selector='input[type="email"]',
            password_selector='input[type="password"]',
            login_submit_selector='button[type="submit"]',
            # Same SSO-button collision as Pulsz — sister site, same
            # React app, same multi-provider login surface where the
            # bare ``button.sso-button`` class can match Facebook
            # before Google in DOM order. Pin to text-based candidates.
            google_oauth_btn_selectors=(
                'button:has-text("Sign in with Google")',
                'button:has-text("Continue with Google")',
                'button:has-text("Log in with Google")',
                '[data-test*="google" i]',
                '[data-testid*="google" i]',
            ),
        ),

        # Same data-test hooks as Pulsz — sister site, same React app.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=['[data-test="header-sweepstakes-value"]'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=['[data-test="header-goldcoins-value"]'],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # MTB chain (verified live):
        #   1. modal_selector → click the wheel wrapper inside the
        #      auto-popped Wheel of Winners modal. The wheel itself
        #      is rendered to a ``<canvas>``; the wrapper div carries
        #      the ``data-test`` hook and is the click target. This
        #      kicks off a ~6-8 second spin animation.
        #   2. (no tab_selector — modal opens directly to the wheel.)
        #   3. btn_selector → "GET MY COINS" button that appears
        #      inside the modal once the wheel settles. Scoped to
        #      ``common-modal`` so the framework's pre-check for
        #      modal_selector visibility is what gates "already
        #      claimed". ``btn_visibility_timeout_ms`` is bumped
        #      because the spin animation is the gating delay.
        #   4. close_btn_selector → after collect, the same modal
        #      slot is replaced with a coin-store upsell. The
        #      standard MUI-style back/close icon dismisses it.
        claim_config=MTBClaimConfig(
            modal_selector='[data-test="common-modal"] [data-test="wheel-of-winners-daily-reward"]',
            tab_selector=None,
            btn_selector='[data-test="common-modal"] button:has-text("GET MY COINS")',
            close_btn_selector='[data-test="common-modal"] button[aria-label="back button"]',
            btn_visibility_timeout_ms=12000,
        ),
        claim_pattern="mtb",

        requires_2fa=False,
        geoip=False,
        solve_cloudflare=False,
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_pulszbingo_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
