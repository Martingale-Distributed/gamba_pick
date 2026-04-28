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

from typing import Dict

from playwright.sync_api import Page

from casino import (
    BrowserError,
    CasinoAccountState,
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


# Map the toast confirmation text → currency code. The site spells
# the names as one word ("SweepsCoins" / "GoldCoins") in the toast.
_SWITCH_TOAST_TO_CODE = (
    ("SweepsCoins", "SC"),
    ("GoldCoins", "GC"),
)
# How long to wait for the post-toggle Toastify confirmation. The
# toast appears within ~200ms of the click on a normal connection;
# 4s gives margin for slow renders without making the read sluggish.
_TOAST_TIMEOUT_MS = 4000


def read_spinquest_balances(page: Page) -> CasinoAccountState:
    """Read SpinQuest's SC + GC balances using toggle + toast.

    SpinQuest's header has a single ``button[data-sentry-component=
    "Amounts"]`` that toggles between SC and GC on each click. After
    each toggle, react-toastify pops a confirmation in
    ``#root > div.Toastify`` reading "You've switched to SweepsCoins"
    or "...GoldCoins". We use that toast as the source of truth for
    which currency is currently displayed — much more stable than
    the per-currency MUI emotion classes (``css-XXX``), which the
    site rebuilds on each deploy.

    Algorithm: hit the toggle twice. After each click, parse the
    newest toast to learn which currency we just switched to, then
    read the displayed value and stash it in ``balances[code]``.
    Two clicks restore the page to its pre-call state.
    """
    balances: Dict[str, float] = {}
    amounts_btn_selector = 'button[data-sentry-component="Amounts"]'
    toast_selector = "#root div.Toastify .Toastify__toast-body"

    # Wait for the Amounts button to mount before we start clicking.
    # SpinQuest's lobby is the slowest of the working set; let it
    # settle before the first toggle.
    try:
        page.wait_for_selector(amounts_btn_selector, state="visible", timeout=30000)
    except BrowserError:
        log.warning(
            "[SpinQuest] Amounts button never appeared; balances will be empty"
        )
        return CasinoAccountState(balances=balances)

    for _ in range(2):
        try:
            page.click(amounts_btn_selector, delay=gaussian_random_delay(), timeout=5000)
        except BrowserError as e:
            log.warning("[SpinQuest] toggle click failed: %s", e)
            break

        # Toast text identifies the currency we just switched TO.
        try:
            page.wait_for_selector(
                toast_selector, state="visible", timeout=_TOAST_TIMEOUT_MS
            )
            toast_text = page.locator(toast_selector).first.text_content() or ""
        except BrowserError:
            log.warning(
                "[SpinQuest] no switch-confirmation toast within %dms; can't map value",
                _TOAST_TIMEOUT_MS,
            )
            continue

        code = next(
            (c for needle, c in _SWITCH_TOAST_TO_CODE if needle in toast_text),
            None,
        )
        if code is None:
            log.warning(
                "[SpinQuest] toast %r didn't match a known currency", toast_text
            )
            continue

        # Read the value now showing in the Amounts button.
        try:
            value_text = (
                page.locator(f"{amounts_btn_selector} p").first.text_content() or ""
            )
        except BrowserError as e:
            log.warning("[SpinQuest] couldn't read value text: %s", e)
            continue

        try:
            n = float(value_text.replace(",", "").strip())
            balances[code] = n
            log.info("Found %s balance: %s", code, n)
        except ValueError:
            log.warning("[SpinQuest] couldn't parse value %r as float", value_text)

        # Wait out the toast so the next iteration's wait_for_selector
        # doesn't re-pick the same toast (Toastify auto-dismisses
        # within ~3s; small fixed wait is fine here).
        page.wait_for_timeout(1500)

    return CasinoAccountState(balances=balances)


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

        # The Amounts button is a single toggle (one click flips
        # between SC and GC) — neither the per-currency ``activator``
        # pattern (Sportzino) nor a stable ``is_active_selector``
        # (the only differentiator is MUI's hashed emotion class,
        # which churns on every site rebuild) work reliably. Instead,
        # ``custom_balance_parser=read_spinquest_balances`` below
        # uses the post-toggle Toastify confirmation as a stable
        # source of truth ("You've switched to SweepsCoins/GoldCoins").
        # The ``Currency`` entries here are documentation only —
        # the custom parser ignores ``currency_display_config``
        # entirely.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(name="Sweeps Coins", code="SC", selectors=[]),
                Currency(name="Gold Coins", code="GC", selectors=[]),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),
        custom_balance_parser=read_spinquest_balances,

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
