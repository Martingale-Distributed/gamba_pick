"""
Fortune Wins Automation Script
==============================

Automates login + daily claim on fortunewins.com (formerly fortunecoins.com,
renamed mid-2026). Same Stake-family / Mediumrare backend as Sportzino
and Zula — same OAuth PKCE flow at ``/login``, same Cloudflare Turnstile
gating, same SLNGApp client_id and ``/AuthCallback`` redirect.

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

from playwright.sync_api import Page

from casino import (
    BrowserError,
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    MTBClaimConfig,
    gaussian_random_delay,
    get_arg_parser,
    log,
)
from scrapling_ext import make_casino_automation


def dismiss_post_login_popup(page: Page) -> None:
    """Dismiss the daily-bonus auto-popup that Fortune Wins shows
    immediately after login.

    The popup has a single "GO TO COIN STORE" CTA on a backdrop that
    intercepts clicks on the header — without dismissing it the MTB
    chain's modal click on ``.coin-store-button`` is blocked. Strategy
    is "first thing that works wins":

      1. Click ``.daily-bonus-dialog-close-button`` if rendered.
      2. Press Escape (most modal libraries respect this).
      3. As a last resort, click the proceed-button — this navigates
         into the same coin-store flow that the MTB modal click would
         open, so the subsequent MTB step is at worst a no-op.

    If the dialog never appears (already claimed today, no daily bonus
    available, etc.) we wait a brief grace window then move on.
    """
    # Give the React state a moment to hydrate the modal after the
    # post-submit navigation. The dialog is typically rendered within
    # 1-2s of landing on the lobby.
    try:
        page.wait_for_selector(
            ".daily-bonus-dialog",
            state="visible",
            timeout=8000,
        )
    except BrowserError:
        log.info("[FortuneWins] No daily-bonus-dialog post-login; nothing to dismiss")
        return

    # Try the dedicated close button first (cleanest dismissal).
    close = page.locator("button.daily-bonus-dialog-close-button").first
    try:
        if close.count() > 0 and close.is_visible():
            close.click(delay=gaussian_random_delay(), timeout=3000)
            log.info("[FortuneWins] Dismissed daily-bonus-dialog via close button")
            return
    except BrowserError:
        pass

    # Most modal libraries listen for Escape on document.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        if not page.locator(".daily-bonus-dialog").first.is_visible():
            log.info("[FortuneWins] Dismissed daily-bonus-dialog via Escape key")
            return
    except BrowserError:
        pass

    # Fallback: click "GO TO COIN STORE" — same destination as the MTB
    # modal click, so the next step is at worst a no-op.
    proceed = page.locator("button.daily-bonus-dialog-proceed-button").first
    try:
        if proceed.count() > 0 and proceed.is_visible():
            proceed.click(delay=gaussian_random_delay(), timeout=3000)
            log.info(
                "[FortuneWins] Closed daily-bonus-dialog via proceed-button "
                "(navigates into coin store)"
            )
            return
    except BrowserError:
        pass

    log.warning(
        "[FortuneWins] daily-bonus-dialog visible but couldn't dismiss it; "
        "MTB chain may fail downstream"
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
            post_login_callback=dismiss_post_login_popup,
        ),

        # Identical to Zula. Both buttons render side-by-side; the
        # active one has ``.active`` and shows the formatted number,
        # the inactive one is empty until the user toggles. text_content
        # of the parent yields ``GC831,899,071`` / ``FC0.06`` style;
        # the shared parser strips the code prefix and commas.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=["div.FCButtonItem.FCoins"],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=["div.FCButtonItem.GCoins"],
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
