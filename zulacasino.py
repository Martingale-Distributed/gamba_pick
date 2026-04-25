"""
Zula Casino Automation Script
=============================

Automates login (and daily claim TBD) on zulacasino.com. Login is a
full-page flow at ``/login`` gated by Cloudflare Turnstile — not a modal
like SpinQuest. Clicking the header "LogIn" button kicks off the OAuth
PKCE redirect to the login page. The "Continue with email" button is
held disabled until Turnstile resolves (Camoufox's stealth mode normally
passes Turnstile's auto-solve).

Zula does NOT use GeoComply or other compliance vendors — only
Cloudflare (Turnstile + insights) and Google/FB analytics. No special
geolocation handling is needed; ``geoip`` defaults to False.

Auth path: Google OAuth
-----------------------
This script is set up to use **Google OAuth** (``--google-oauth``).
Form-based email+password login also works, but Zula gates it behind
Cloudflare Turnstile — the OAuth path avoids the captcha entirely.

First-time setup:

  python zulacasino.py --google-oauth --setup

``--setup`` launches a non-headless browser, runs ``pre_login`` to land
on Zula's ``/login`` page, and then pauses so you can click "Sign in
with Google", complete 2FA, grant site consent, etc. Press Enter in the
terminal once you're fully signed in — the browser session persists to
``./profiles/zulacasino/`` (the default when ``--user-data-dir`` isn't
specified) and the script exits without claiming.

Normal runs after setup:

  python zulacasino.py --google-oauth [--headless]

The Google consent screen is skipped because the cookies are already in
the profile dir. Override with ``--user-data-dir <path>`` if you want a
different profile location.

Credentials for the form-login path (non-OAuth) come from ``.env`` via
``ZULACASINO_USERNAME`` / ``ZULACASINO_PASSWORD``.
"""

from playwright.sync_api import Page

from casino import (
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


def click_header_login(page: Page) -> None:
    """Click the header LogIn button to start the OAuth login flow.

    Zula's login is a full-page redirect (not a modal), so clicking the
    button kicks off navigation to ``/login?ReturnUrl=...&code_challenge=...``
    which includes the OAuth PKCE nonce generated client-side — we can't
    just navigate directly to ``/login`` without losing that.

    Uses ``no_wait_after=True`` so Playwright doesn't hold a pending
    after-click promise waiting for navigation (that promise, if it
    times out, crashes the Node driver while Python is blocked on
    ``input()`` in ``--setup`` mode). We wait for the ``/login`` URL
    explicitly instead.
    """
    try:
        btn = page.locator("button.unauthorized-header-login-btn").first
        if btn.count() > 0 and btn.is_visible():
            btn.click(
                delay=gaussian_random_delay(),
                timeout=10000,
                no_wait_after=True,
            )
            try:
                page.wait_for_url("**/login*", timeout=15000)
                log.info("Reached Zula /login page")
            except Exception:
                log.info(
                    "Timeout waiting for /login URL; current url: %s", page.url
                )
        else:
            log.info("Header login button not visible; may already be on /login")
    except Exception as e:
        log.warning("Could not click header login button: %s", str(e))


def create_zulacasino_config() -> CasinoConfig:
    return CasinoConfig(
        name="ZulaCasino",
        # No www — so url_to_env_prefix yields ZULACASINO (not WWW).
        url="https://zulacasino.com",
        login_url="https://zulacasino.com/login",
        description="Zula Casino Automation",

        login=LoginConfig(
            # The email input accepts either email or username; name="username"
            # is the attribute the form actually uses.
            username_selector='input[name="username"]',
            password_selector='input[name="password"]',
            # Prefer the semantic class over data-sentry-element here because
            # the page also has a separate Google OAuth button that would
            # match ZulaButton. The class is specific to the email-submit CTA.
            # The button is disabled until Cloudflare Turnstile resolves;
            # Playwright's click() waits for the enabled state.
            login_submit_selector='button.login-form-content-email-submit',
            pre_login_callback=click_header_login,
        ),

        # Zula shows both balances simultaneously in the header — no
        # dropdown or switcher needed. Each `FCButtonItem` div has a
        # currency-specific class (`.GCoins` / `.FCoins`) and its text is
        # the code prefix followed by the formatted amount, e.g.
        # `GC5,163,117` or `SC0.06`. The shared balance parser strips
        # the currency.code prefix and commas before float-parsing.
        # NOTE: Sweeps Coins is called "Free Coins" (FCoins) internally.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=['div.FCButtonItem.FCoins'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=['div.FCButtonItem.GCoins'],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # Daily claim lives in the Coin Store, opened by drilling down
        # through the header. This is always-nav, which handles the case
        # where the first-login-of-day auto-popup didn't fire (already
        # dismissed, not the first session, etc.) — see MTBClaimConfig
        # semantics: modal → tab → button (→ close).
        #
        # Click chain:
        #   1. modal_selector: any coin-balance button in header → opens
        #      a `.balance__container` popover with Redeem SC / Get Coins
        #      action buttons.
        #   2. tab_selector: "Get Coins" in that popover → opens the
        #      Coin Store overlay containing the daily bonus package.
        #   3. btn_selector: COLLECT on the reward package → claims it.
        #      MTB flow is is_disabled-aware, so if the bonus is already
        #      claimed (button disabled), the flow logs and skips cleanly.
        #   4. close_btn_selector: `.dialog-close-button` closes the store.
        claim_config=MTBClaimConfig(
            modal_selector='button.FCButtonText',
            tab_selector='button.balance__button:has-text("Get Coins")',
            btn_selector='button.coin-store-reward-package-footer-button',
            close_btn_selector='button.dialog-close-button',
        ),
        claim_pattern="mtb",

        requires_2fa=False,
        # No compliance-vendor geo gate on Zula; default browser geo is fine.
        geoip=False,
        # Scrapling's solve_cloudflare runs against the initial fetch URL
        # (the homepage), which has no challenge — Zula's Turnstile is on
        # the subsequent /login page that pre_login navigates to. The hook
        # doesn't fire there, so it ends up noisy (logs a spurious ERROR
        # and adds latency) without solving the real thing. Keep False and
        # rely on our own `wait_for_turnstile` at the right point in the
        # sequence.
        solve_cloudflare=True,
        # Camoufox backend. The Chrome backend was tried as a Turnstile-
        # auto-pass workaround (see scrapling_ext.py for DynamicSession
        # plumbing) but also failed in practice — keeping the plumbing
        # around for future sites that might benefit.
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_zulacasino_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
