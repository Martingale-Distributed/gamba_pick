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
Turnstile and the OAuth path is simpler and avoids the captcha. Pair
``--google-oauth`` with ``--user-data-dir`` pointed at a Camoufox/Firefox
profile that already has an authenticated Google session so no
credentials need to be typed each run.

First-time setup for OAuth:
  1. Create a persistent profile dir, e.g. ``~/.gamba_pick/zula_profile``.
  2. Run once with ``--google-oauth --user-data-dir <path>`` — when the
     Google sign-in flow appears, complete it manually in the browser.
  3. Subsequent runs with the same ``--user-data-dir`` reuse the session.

Alternatively, set ``GOOGLE_EMAIL`` / ``GOOGLE_PASSWORD`` in ``.env`` and
the OAuth page action will fill them when the redirect happens. 2FA on
the Google account will block this; persistent ``user_data_dir`` is
preferred.

Credentials for form login (non-OAuth path) come from ``.env`` via
``ZULACASINO_USERNAME`` / ``ZULACASINO_PASSWORD``.

TODOs (post-login scouting needed):
  - Daily claim button selector (likely a CTA on the homepage after auth,
    similar to SpinQuest's HomeCtaCardButton).
  - Currency-switcher selector + pattern (dropdown vs. click-to-cycle).

Usage
-----
python zulacasino.py --google-oauth --user-data-dir <profile> [--headless] [--skip-claim]
"""

from playwright.sync_api import Page

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    SimpleClaimConfig,
    gaussian_random_delay,
    get_arg_parser,
    log,
)
from scrapling_ext import make_casino_automation


def click_header_login(page: Page) -> None:
    """Click the header LogIn button to start the OAuth login flow.

    Zula's login is a full-page redirect (not a modal), so this kicks off
    the navigation to ``/login?ReturnUrl=...&code_challenge=...`` before
    the framework fills credentials. The ``unauthorized-header-login-btn``
    class is the stable hook; the element also has ``id=TEST_LOADING_BUTTON``
    which is less semantically appropriate despite being stable.
    """
    try:
        btn = page.locator("button.unauthorized-header-login-btn").first
        if btn.count() > 0 and btn.is_visible():
            btn.click(delay=gaussian_random_delay(), timeout=5000)
            page.wait_for_timeout(1000)
            log.info("Clicked Zula header login button")
        else:
            log.info("Header login button not visible; may already be on /login")
    except Exception as e:
        log.warning("Could not click header login button: %s", str(e))


def create_zulacasino_config() -> CasinoConfig:
    return CasinoConfig(
        name="ZulaCasino",
        # No www — so url_to_env_prefix yields ZULACASINO (not WWW).
        url="https://zulacasino.com",
        login_url="https://zulacasino.com",
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

        # TODO: verify post-login. Common Zula pattern is a coin-switcher
        # in the header; selectors below are PLACEHOLDERS until we can scout
        # the authenticated UI. For first run use --skip-claim and grab the
        # HTML of the switcher + claim button.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=['[data-sentry-component*="CurrencyBalance"]'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=['[data-sentry-component*="CurrencyBalance"]'],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # TODO: placeholder. Replace with the real daily-claim CTA once
        # scouted from a logged-in session.
        claim_config=SimpleClaimConfig(
            btn_selector='button:has-text("claim")',
        ),
        claim_pattern="simple",

        requires_2fa=False,
        # No compliance-vendor geo gate on Zula; default browser geo is fine.
        geoip=False,
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_zulacasino_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
