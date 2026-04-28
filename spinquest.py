"""
SpinQuest Casino Automation Script
==================================

Automates login and daily $1 SC claim on spinquest.com. The claim surface
is a single "claim now" CTA on the homepage, so this uses the `simple`
claim pattern rather than the wallet-modal (`mtb`) or modal-cascade
(`generic`) patterns used by Stake.us / LuckyBird.

SpinQuest uses GeoComply for regulatory-grade geolocation compliance,
which cross-checks IP, WebRTC, browser fingerprint, and navigator
geolocation against one another. For this to pass, the automation must
present the site with a coherent "real Firefox in real location" picture
rather than a stealth-masked browser with synthetic coordinates.

This config targets the "legitimate user on their own laptop" use case:
- `geoip=True` makes Camoufox derive coords + timezone + locale from the
  actual connection IP, so every cross-check agrees internally.
- No `set_geolocation` override — Camoufox's IP-derived coords flow
  through to `navigator.geolocation` unmodified.
- No proxy recommended — the point is that the user IS in an allowed
  state and we want GeoComply to see that as the truth it is.

Credentials come from picks.env via SPINQUEST_USERNAME /
SPINQUEST_PASSWORD (prefix derived from the domain by url_to_env_prefix).

Usage
-----
python spinquest.py [--headless] [--skip-claim] [--user-data-dir PATH]
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
    make_grant_geolocation_permission,
)
from scrapling_ext import make_casino_automation


def open_login_modal(page: Page) -> None:
    """Open SpinQuest's login modal by clicking the header LOGIN button.

    Runs as the pre-login callback's second step (after the geolocation
    permission grant). The header button has the stable `loginBtn` class;
    MUI's hashed css-* classes are avoided since they churn across builds.
    """
    try:
        btn = page.locator("button.loginBtn").first
        if btn.count() > 0 and btn.is_visible():
            btn.click(delay=gaussian_random_delay(), timeout=5000)
            page.wait_for_timeout(800)
            log.info("Opened SpinQuest login modal")
        else:
            log.info("Login button not visible; modal may already be open")
    except Exception as e:
        log.warning("Could not open login modal: %s", str(e))


_grant_geo = make_grant_geolocation_permission(origin="https://spinquest.com")


def pre_login(page: Page) -> None:
    """Grant geo permission (no coord override), then open the login modal."""
    _grant_geo(page)
    open_login_modal(page)


def create_spinquest_config() -> CasinoConfig:
    return CasinoConfig(
        name="SpinQuest",
        url="https://spinquest.com",
        login_url="https://spinquest.com",
        description="SpinQuest Casino Automation",

        login=LoginConfig(
            username_selector='input[name="emailOrUsername"]',
            password_selector='input[name="password"]',
            # data-sentry-element is a stable hook the site ships in prod;
            # preferring it over MUI class names which are hashed per build.
            login_submit_selector='button[type="submit"][data-sentry-element="LoadingButtonBase"]',
            pre_login_callback=pre_login,
        ),

        # The `amounts` button is a click-to-cycle switcher: one button
        # that toggles between SC and GC each click. We use the
        # framework's per-currency ``activate_selector`` (the button) +
        # ``is_active_selector`` (a marker only present when this
        # currency is the active one) so the loop is robust to
        # whichever currency happens to be active when we land on the
        # page:
        #   - if the wanted currency is already active → skip the click
        #   - otherwise → click once to toggle to it, then read
        #
        # The ``is_active_selector`` is the per-currency MUI emotion
        # class (``css-179u6ap`` / ``css-17oy78s``). Those hashes are
        # stable across reloads but can change when SpinQuest rebuilds
        # — re-probe and update if balances start coming back wrong.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    activate_selector='button[data-sentry-component="Amounts"]',
                    is_active_selector='button[data-sentry-component="Amounts"] p.css-179u6ap',
                    selectors=['button[data-sentry-component="Amounts"] p'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    activate_selector='button[data-sentry-component="Amounts"]',
                    is_active_selector='button[data-sentry-component="Amounts"] p.css-17oy78s',
                    selectors=['button[data-sentry-component="Amounts"] p'],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        claim_config=SimpleClaimConfig(
            btn_selector='button[data-sentry-element="HomeCtaCardButton"]:has-text("claim")',
        ),
        claim_pattern="simple",

        requires_2fa=False,
        # Camoufox derives coords+timezone+locale from the real IP so every
        # GeoComply cross-check (IP vs. navigator.geolocation vs. WebRTC vs.
        # timezone) agrees. Required to pass the regulatory-grade geo gate.
        geoip=True,
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_spinquest_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
