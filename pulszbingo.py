"""
Pulsz Bingo Casino Automation Script
====================================

Automates login + daily claim on www.pulszbingo.com. Pulsz Bingo is the
sister site of Pulsz — same React + CSS-modules frontend, same operator,
**identical** ``data-test`` hooks for currency display
(``header-goldcoins-value`` / ``header-sweepstakes-value``).

Daily-claim mechanic differs from Pulsz, though: where Pulsz uses a
7-day streak modal with a "GET FREE COINS" CTA, Pulsz Bingo's lobby
ships a ``[data-test="wheel-of-winners-daily-reward"]`` widget — a
prize wheel rather than a streak grid. The exact click flow on the
wheel hasn't been fully mapped yet; the claim selector below is a
best-effort that the runtime log will validate.

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
    SimpleClaimConfig,
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

        # Daily-claim flow is wheel-based — Pulsz Bingo ships a
        # ``[data-test="wheel-of-winners-daily-reward"]`` widget on
        # the lobby instead of Pulsz's streak modal. The exact click
        # path through the wheel (spin → result → collect) hasn't
        # been mapped yet; placeholder selector targets the wheel
        # widget itself, which lets the framework log "claim button
        # not visible" cleanly when the wheel doesn't surface a
        # clickable claim CTA. Will be refined once the live flow
        # is observed.
        claim_config=SimpleClaimConfig(
            btn_selector='[data-test="wheel-of-winners-daily-reward"] button',
        ),
        claim_pattern="simple",

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
