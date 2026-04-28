"""
Fortune Wins Automation Script
==============================

Automates login + daily claim on fortunewins.com (formerly fortunecoins.com,
renamed mid-2026). On the same SLNGApp OAuth platform as Sportzino and
Zula — same OAuth PKCE flow at ``/login``, same Cloudflare Turnstile
gating, same ``client_id=SLNGApp`` and ``/AuthCallback`` redirect.

Class scheme is closer to Zula than to Sportzino:

  * Currency display uses the same ``FCButtonItem`` wrapper with
    ``.FCoins`` / ``.GCoins`` modifier classes that Zula does. The
    text format is ``GC831,899,071`` — currency code prefix + commas.
  * Daily claim is **MTB**, accessed via the GET COINS button in the
    header rather than a notification toast or dedicated dialog. The
    coin store has two tabs (PACKAGES / FREE COINS); the FREE COINS
    tab has the daily ``COLLECT`` button.

Login form has Sportzino's name attributes (``username`` / ``password``)
but the submit button class is ``loginFormButtton`` — note the typo
(three ``t``s); that's the actual class name in the rendered DOM.

The homepage Log In button has no site-specific class (just
``loadingBtn btn btn-secondary``), so this script intentionally leaves
``pre_login_click_selector`` unset — the framework falls back to
``selectors_generic.HEADER_LOGIN_BUTTON``, whose ``button:has-text("Log
in")`` candidate matches by text.

First-time setup:

  python fortunewins.py --google-oauth --setup

Normal runs:

  python fortunewins.py --google-oauth [--headless]
"""

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    MTBClaimConfig,
    get_arg_parser,
    make_dismiss_popup,
)
from scrapling_ext import make_casino_automation


# Fortune Wins auto-shows ``.daily-bonus-dialog`` immediately after
# login (single "GO TO COIN STORE" CTA, backdrop intercepts header
# clicks). Without dismissal the MTB modal click on
# ``.coin-store-button`` gets blocked.
#
# Close button is the SLNGApp-platform-shared ``button.close-popup-button``
# (rendered as × via CSS rotation of a literal "+"). That class is in
# selectors_generic.MODAL_CLOSE_BUTTON, so leaving ``close_selector``
# unset lets the generic step find it. ``fallback_selector`` is the
# "GO TO COIN STORE" CTA — last-resort path that navigates into the
# coin-store flow MTB then resumes from cleanly.
_dismiss_daily_bonus_popup = make_dismiss_popup(
    modal_selector=".daily-bonus-dialog",
    fallback_selector='button:has-text("GO TO COIN STORE")',
    name="FortuneWins.daily-bonus-dialog",
)


def create_fortunewins_config() -> CasinoConfig:
    return CasinoConfig(
        name="FortuneWins",
        # Apex host, no www redirect to worry about (unlike Zula).
        url="https://fortunewins.com",
        login_url="https://fortunewins.com",
        description="Fortune Wins Automation",

        login=LoginConfig(
            # Same as Sportzino: name=username (id=emailAddress), name=password.
            username_selector='input[name="username"]',
            password_selector='input[name="password"]',
            # Note the typo — three ``t``s. That's the actual class.
            login_submit_selector="button.loginFormButtton",
            # ``pre_login_click_selector`` deliberately unset — the
            # homepage button has no site-specific class, so we lean on
            # selectors_generic.HEADER_LOGIN_BUTTON's text-based
            # fallback (``button:has-text("Log in")``). First real
            # exercise of the step-2 generic-fallback path.
            #
            # Fortune Wins shows a daily-bonus auto-popup right after
            # login whose backdrop blocks the header. Dismiss it before
            # MTB tries to click ``.coin-store-button``.
            post_login_callback=_dismiss_daily_bonus_popup,
        ),

        # Same inline-toggle shape as Sportzino: the header shows only
        # the *active* currency's value; the inactive side renders an
        # empty button. Clicking the currency's own ``FCButtonText``
        # button activates it (idempotent if already active). Each
        # active button renders both a ``.textDecimals.mobile``
        # (abbreviated ``832499K``) and a ``.textDecimals.desktop``
        # (full ``832,499,071``) — CSS picks one visually but
        # ``textContent`` concatenates both. Scope to
        # ``.textDecimals.desktop`` to read only the comma-formatted
        # full number.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    activate_selector="div.FCButtonItem.FCoins button.FCButtonText",
                    selectors=["div.FCButtonItem.FCoins .textDecimals.desktop"],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    activate_selector="div.FCButtonItem.GCoins button.FCButtonText",
                    selectors=["div.FCButtonItem.GCoins .textDecimals.desktop"],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # MTB chain (verified live in the authenticated lobby):
        #   1. .coin-store-button          → opens coin store popup
        #   2. .coin-store-tab "FREE COINS" → switches to free-coins tab
        #   3. .daily-bonus-collect-button → COLLECT (the actual claim)
        #   4. .close-popup-button         → close the store
        # is_disabled-aware in MTB, so a re-claim attempt after today's
        # bonus is already collected logs and skips cleanly.
        claim_config=MTBClaimConfig(
            modal_selector=".coin-store-button",
            tab_selector='button.coin-store-tab:has-text("FREE COINS")',
            btn_selector="button.daily-bonus-collect-button",
            close_btn_selector="button.close-popup-button",
        ),
        claim_pattern="mtb",

        requires_2fa=False,
        # No GeoComply on Fortune Wins; default browser geo is fine.
        geoip=False,
        # Turnstile handled by our own ``wait_for_turnstile`` (active
        # checkbox-click solver in casino.py), not scrapling's. False
        # disables scrapling's pre-action attempt that fires too early.
        solve_cloudflare=False,
        # Camoufox for fingerprint consistency with Zula. Chrome would
        # also work — flip if Turnstile escalates often.
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_fortunewins_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
