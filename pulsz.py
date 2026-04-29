"""
Pulsz Casino Automation Script
==============================

Automates login + daily claim on www.pulsz.com. Pulsz is a sweeps
casino with a React + CSS-modules frontend (class names hashed per
build, e.g. ``styles-module-scss-module__XpbeMW__coinsContainer``)
— **don't** target the hashed classes. Pulsz instead ships stable
``data-test`` hooks throughout the lobby, which is what this config
keys off of.

Auth path: Google OAuth
-----------------------
This script supports Google OAuth when run with ``--google-oauth``
(off by default — the framework's generic ``argparse`` doesn't
flip ``google_oauth=True`` per-site, so manual users have to opt in
explicitly). The login link in the header is a plain
``<a href="/login">`` — direct navigation to ``login_url`` lands on
the OAuth-capable login page where the framework's generic
``GOOGLE_OAUTH_BUTTON`` candidates match.

First-time setup (interactive — driver pauses for you to complete
Google sign-in + 2FA + site consent):

  python pulsz.py --google-oauth --setup

Normal headless runs after setup:

  python pulsz.py --google-oauth [--headless]

Currency display (verified live in the authenticated lobby):

    [data-test="header-goldcoins-value"]    — Gold Coins, e.g. "GC 7,500"
    [data-test="header-sweepstakes-value"]  — Sweeps Coins, e.g. "SC 0.45"

Daily claim
-----------
Pulsz auto-shows a 7-day streak modal (``[data-test="common-modal"]``)
on each login. Today's tile carries a ``GET FREE COINS`` button —
clicking claims today's reward. After collect, the same modal slot
is replaced with a coin-store upsell ("BUY NOW $X.XX") which we
dismiss via the standard ``button[aria-label="back button"]`` close
icon. If today's bonus has already been claimed, the streak modal
doesn't auto-pop on subsequent logins — only the upsell appears —
and the framework cleanly reports ``already_claimed`` since the
``GET FREE COINS`` button isn't visible.
"""

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    SimpleClaimConfig,
    get_arg_parser,
    make_dismiss_popup_stack,
)
from scrapling_ext import make_casino_automation


# Pulsz's lobby auto-pops a coin-store upsell ("BUY NOW $X.XX") in
# the same ``[data-test="common-modal"]`` slot that hosts the daily-
# claim streak modal. The upsell shows up regardless of claim status
# (immediately on already-claimed days; right after collect on fresh
# days). Left up, it blocks DOM interactions for any subsequent step.
# Content-filter on ``"buy now"`` ensures we never stomp on a live
# streak modal that's still waiting on ``GET FREE COINS``.
_dismiss_pulsz_upsell = make_dismiss_popup_stack(
    modal_selector='[data-test="common-modal"]',
    close_selector='[data-test="common-modal"] button[aria-label="back button"]',
    name="Pulsz.upsell",
    content_filter="buy now",
    initial_wait_ms=3000,
)


def create_pulsz_config() -> CasinoConfig:
    return CasinoConfig(
        name="Pulsz",
        # Pulsz only serves www; apex isn't a separate redirect target
        # worth optimizing around.
        url="https://www.pulsz.com",
        # Direct-navigate to /login. The header login link is a plain
        # ``<a href="/login">`` so there's no PKCE-nonce minting on the
        # button click that we'd lose by skipping it (unlike SLNGApp).
        login_url="https://www.pulsz.com/login",
        description="Pulsz Casino Automation",

        login=LoginConfig(
            # Form-login path placeholders — only used when
            # ``--google-oauth`` is OFF. The user's account is provisioned
            # via Google so the form path is untested here; generic
            # type-based selectors give a reasonable starting point if
            # someone ever switches.
            username_selector='input[type="email"]',
            password_selector='input[type="password"]',
            login_submit_selector='button[type="submit"]',
            # Pulsz's login page exposes multiple SSO buttons (Google,
            # Facebook, Apple). The generic ``_GENERIC_GOOGLE_OAUTH_BUTTON``
            # list has ``button.sso-button`` (plain class, no Google
            # modifier) early in priority — that catches Pulsz's
            # Facebook button, since SSO buttons here apparently share
            # the bare class with no provider-specific modifier. Pin
            # to text-based candidates to disambiguate.
            google_oauth_btn_selectors=(
                'button:has-text("Sign in with Google")',
                'button:has-text("Continue with Google")',
                'button:has-text("Log in with Google")',
                '[data-test*="google" i]',
                '[data-testid*="google" i]',
            ),
            # Sweep the post-login coin-store upsell before balance
            # read / claim attempt — see ``_dismiss_pulsz_upsell``
            # docstring above. Content-filtered so a live streak
            # claim modal sharing the slot is left untouched.
            post_login_callback=_dismiss_pulsz_upsell,
        ),

        # Stable ``data-test`` selectors — the only safe choice here.
        # The CSS-module class names re-hash on every build, so any
        # ``styles-module-scss-module__XXXXXX__*`` selector would
        # break on the next deploy.
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

        # Pulsz auto-shows a 7-day streak modal at
        # ``[data-test="common-modal"]`` on each fresh login. Today's
        # tile carries a "GET FREE COINS" button — click that and
        # we're done. Scoped to the modal so an unrelated page button
        # wouldn't accidentally match. Once today is claimed, the
        # streak modal stops auto-popping (only the coin-store upsell
        # shows) so the selector finds nothing and the runner
        # registers ``already_claimed``.
        #
        # ``post_claim_close_selector`` dismisses the upsell that
        # auto-pops in the same modal slot right after collect — keeps
        # the lobby DOM clean for any teardown work.
        claim_config=SimpleClaimConfig(
            btn_selector='[data-test="common-modal"] button:has-text("GET FREE COINS")',
            post_claim_close_selector='[data-test="common-modal"] button[aria-label="back button"]',
        ),
        claim_pattern="simple",

        requires_2fa=False,
        # No GeoComply hooks identified; default browser geo is fine.
        geoip=False,
        # No Cloudflare Turnstile observed on Pulsz's login surface.
        solve_cloudflare=False,
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_pulsz_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
