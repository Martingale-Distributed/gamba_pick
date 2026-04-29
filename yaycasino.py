"""
Yay Casino Automation Script
============================

Automates login + daily claim on yaycasino.com. Confirmed via /login
URL inspection to be on the **SLNGApp** OAuth-PKCE platform — same
``client_id=SLNGApp`` redirect, same ``/AuthCallback`` callback,
same Cloudflare Turnstile checkpoint, same ``loginFormButtton``
typo'd submit class. Closer to FortuneWins than to Zula, since
Yay Casino's redeemable currency is **FC** (Fortune Coins, $0.01
each), not SC.

Login form (verified via DOM probe at https://yaycasino.com/login):
    input[name="username"]  (id=emailAddress)
    input[name="password"]  (id=password)
    button.loginFormButtton (text "Log In", three t's, same class
                             FortuneWins uses)
Google SSO at ``button.sso-button.login-sso-google`` for users who
registered via Google — picked at runtime via ``--google-oauth``.

Currency display (verified in the authenticated lobby):
    div.FCButtonItem.FCoins   — Fortune Coins (active toggles to
                                ``.active``)
    div.FCButtonItem.GCoins   — Gold Coins
Inner shape: ``> button.FCButtonText > div.textDecimals > span``.
Both wrappers render their value concurrently — unlike FortuneWins's
mobile/desktop dual-span split (``.textDecimals.desktop`` /
``.textDecimals.mobile``), Yay Casino has a single bare
``.textDecimals``. The inactive side just gets dimmed by an
``.inactive-overlay`` sibling but its value stays in the DOM.

Daily-claim chain (verified in the lobby):
    button.buttonBuy                     — opens the coin-store
                                           modal (different class
                                           from FortuneWins's
                                           ``.coin-store-button``)
    [no tab click]                       — modal opens directly
                                           on the FREE COINS view
                                           (``.coinsModal.freecoins``)
    button.daily-bonus-collect-button    — Collect (same class
                                           FortuneWins uses)
    button.closePopupButton              — Close (one word, no
                                           hyphens — different from
                                           FortuneWins's
                                           ``.close-popup-button``)

First-time setup:

  python yaycasino.py --setup

Normal runs:

  python yaycasino.py [--headless]
"""

from playwright.sync_api import Page

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    MTBClaimConfig,
    get_arg_parser,
    log,
)
from scrapling_ext import make_casino_automation


def goto_lobby(page: Page) -> None:
    """Navigate to /lobby so the currency bar mounts.

    YayCasino's logged-in homepage at ``/`` doesn't render the
    ``FCButtonItem`` currency bar — only ``/lobby`` does. The OAuth
    callback drops us back at ``/`` by default, so the balance-read
    step would otherwise time out waiting for selectors that never
    mount. One ``page.goto`` here puts the lobby in the DOM before
    ``make_get_casino_account_state`` runs.

    Wait for the OAuth callback to finish before navigating —
    intercepting the callback redirect mid-flight drops the session
    cookie and we end up at ``/public-lobby/lobby`` (logged-out).
    """
    # Wait for OAuth callback chain to settle on a stable URL before
    # any further navigation. The callback path hits us as
    # ``/login`` → ``/authcallback?code=...`` → final landing — and
    # if we navigate before /authcallback redirects out, the session
    # cookie never lands and we end up at ``/public-lobby/lobby``
    # (logged out). URL match is case-insensitive — the redirect
    # path is lower-case ``/authcallback`` even though the OAuth
    # request URI uses ``/AuthCallback``.
    def settled(u: str) -> bool:
        lo = u.lower()
        return "/authcallback" not in lo and "/login" not in lo

    try:
        page.wait_for_url(settled, timeout=15000)
    except Exception:
        log.warning(
            "[YayCasino] OAuth callback didn't settle in 15s; current url=%s",
            page.url,
        )

    if "/lobby" in page.url and "public-lobby" not in page.url:
        return
    log.info("[YayCasino] Navigating to /lobby for balance read")
    page.goto("https://www.yaycasino.com/lobby", wait_until="domcontentloaded")


def create_yaycasino_config() -> CasinoConfig:
    return CasinoConfig(
        name="YayCasino",
        # Apex; site redirects to www.yaycasino.com but the login
        # flow tolerates either entry point.
        url="https://yaycasino.com",
        login_url="https://www.yaycasino.com",
        description="Yay Casino Automation",

        login=LoginConfig(
            # Confirmed identical to FortuneWins / Sportzino — name=
            # username (id=emailAddress), name=password.
            username_selector='input[name="username"]',
            password_selector='input[name="password"]',
            # Three t's. Same SLNGApp typo across the platform.
            login_submit_selector="button.loginFormButtton",
            # Header LOG IN button has the SLNGApp-shared class
            # ``loadingBtn btn btn-secondary`` plus a Yay-specific
            # ``sp_login-home-header-top``. Leaving this unset lets
            # the framework's HEADER_LOGIN_BUTTON text fallback
            # match (button:has-text("Log in")).
            #
            # No post-login popup observed — unlike FortuneWins,
            # YayCasino doesn't auto-show a daily-bonus dialog after
            # login. The post-login hook here is purely a navigation
            # to ``/lobby`` because the post-OAuth callback drops us
            # at ``/``, which doesn't render the currency bar.
            post_login_callback=goto_lobby,
        ),

        # ``FCButtonItem`` wrapper with ``.FCoins`` / ``.GCoins``
        # modifier classes and a single bare ``.textDecimals`` for
        # the value. (FortuneWins splits the same span into
        # ``.textDecimals.desktop`` and ``.textDecimals.mobile``;
        # Yay Casino doesn't.) Both wrappers render their value
        # concurrently — keeping ``activate_selector`` purely for
        # parity with the rest of the SLNGApp family.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    # 1 FC = $0.01 (FortuneWins-style redeemable
                    # currency), distinct from SC ($1 each) on
                    # Sportzino / Zula.
                    name="Fortune Coins",
                    code="FC",
                    activate_selector="div.FCButtonItem.FCoins button.FCButtonText",
                    selectors=["div.FCButtonItem.FCoins .textDecimals"],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    activate_selector="div.FCButtonItem.GCoins button.FCButtonText",
                    selectors=["div.FCButtonItem.GCoins .textDecimals"],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # MTB chain (verified live in the lobby):
        #   1. button.buttonBuy                  → opens coin-store modal
        #   2. (no tab click — opens directly on .coinsModal.freecoins)
        #   3. button.daily-bonus-collect-button → Collect
        #   4. button.closePopupButton           → close (one word,
        #                                          different from
        #                                          FortuneWins)
        claim_config=MTBClaimConfig(
            modal_selector="button.buttonBuy",
            tab_selector=None,
            btn_selector="button.daily-bonus-collect-button",
            close_btn_selector="button.closePopupButton",
        ),
        claim_pattern="mtb",

        requires_2fa=False,
        # No GeoComply hooks identified; default browser geo is fine.
        geoip=False,
        # Camoufox-friendly Turnstile handled by our own solver.
        solve_cloudflare=False,
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_yaycasino_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
