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

from gamba_pick.casino import (
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
from gamba_pick.scrapling_ext import make_casino_automation


def open_login_modal(page: Page) -> None:
    """Open SpinQuest's login modal by clicking the header LOGIN button.

    Runs as the pre-login callback's second step (after the geolocation
    permission grant). The header button has the stable `loginBtn` class;
    MUI's hashed css-* classes are avoided since they churn across builds.
    """
    try:
        btn = page.locator("button.loginBtn").first
        if btn.count() > 0 and btn.is_visible():
            # ``no_wait_after=True`` — modal-open click, no navigation;
            # see make_dismiss_popup commentary for the orphan-promise
            # gotcha.
            btn.click(
                delay=gaussian_random_delay(),
                timeout=5000,
                no_wait_after=True,
            )
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


def read_spinquest_balances(page: Page) -> CasinoAccountState:
    """Read SpinQuest's SC + GC balances by toggling and classifying.

    SpinQuest's header has a single ``button[data-sentry-component=
    "Amounts"]`` that toggles between SC and GC on each click. We
    toggle twice (returning the UI to its pre-call state) and read
    the displayed value each time, then classify each value by its
    format:

      * Strings with a decimal point (e.g. ``3.05``) → SC. Sweeps
        Coins are dollar-equivalent and always display two decimals.
      * Strings without a decimal (e.g. ``1,090,000``) → GC. Gold
        Coins are integer-formatted with thousands separators.

    The classifier is locale-independent — earlier attempts using
    the post-toggle Toastify text ("You've switched to ...") broke
    when Camoufox's geo-derived locale switched the message to
    Spanish. The numeric format stays put.

    The MUI emotion class hashes (``css-179u6ap`` for SC,
    ``css-17oy78s`` for GC) are also avoided since they churn on
    every site rebuild.
    """
    balances: Dict[str, float] = {}
    amounts_btn_selector = 'button[data-sentry-component="Amounts"]'

    # Wait for the Amounts button to mount before we start clicking.
    # SpinQuest's lobby is the slowest of the working set — login +
    # full hydration regularly takes 30-50s on cold sessions, so the
    # window has to be generous to avoid spurious empty reads.
    try:
        page.wait_for_selector(amounts_btn_selector, state="visible", timeout=60000)
    except BrowserError:
        log.warning(
            "[SpinQuest] Amounts button never appeared after 60s; "
            "balances will be empty"
        )
        return CasinoAccountState(balances=balances)

    # Track successful toggles so the restore step at the end can
    # leave the UI in its initial state regardless of where the
    # loop bailed out — an unconditional final click would flip
    # past the start when no toggle ever succeeded.
    toggles_done = 0
    for i in range(2):
        if i > 0:
            try:
                # ``no_wait_after=True`` defends against the orphan-
                # promise driver crash documented on
                # ``make_dismiss_popup``.
                page.click(
                    amounts_btn_selector,
                    delay=gaussian_random_delay(),
                    timeout=5000,
                    no_wait_after=True,
                )
                toggles_done += 1
                # Brief wait for the value display to update after
                # the toggle.
                page.wait_for_timeout(500)
            except BrowserError as e:
                log.warning("[SpinQuest] toggle click failed: %s", e)
                break

        try:
            value_text = (
                page.locator(f"{amounts_btn_selector} p").first.text_content() or ""
            ).strip()
        except BrowserError as e:
            log.warning("[SpinQuest] couldn't read value text: %s", e)
            continue

        # Classify by format — decimal => SC, integer => GC.
        if "." in value_text:
            code = "SC"
        elif value_text:
            code = "GC"
        else:
            log.warning("[SpinQuest] empty value text on iter %d", i)
            continue

        try:
            n = float(value_text.replace(",", ""))
        except ValueError:
            log.warning("[SpinQuest] couldn't parse value %r as float", value_text)
            continue

        if code in balances:
            log.warning(
                "[SpinQuest] re-read same currency code %s on iter %d "
                "(both reads classified the same way: was %s, now %s); "
                "toggle may not have flipped",
                code, i, balances[code], n,
            )
        balances[code] = n
        log.info("Found %s balance: %s", code, n)

    # Restore initial UI state: the toggle is binary, so if we did
    # an odd number of successful toggles we need one more to get
    # back; an even count (including zero) is already balanced.
    if toggles_done % 2 == 1:
        try:
            page.click(
                amounts_btn_selector,
                delay=gaussian_random_delay(),
                timeout=5000,
                no_wait_after=True,
            )
        except BrowserError:
            pass

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
        # toggles, reads the displayed value, and classifies by
        # numeric format (decimal => SC, integer => GC). The
        # ``Currency`` entries here are documentation only — the
        # custom parser ignores ``currency_display_config`` entirely.
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
        # Switched from ``camoufox`` (Firefox-based) to the system's
        # installed Chrome on 2026-05-01. Empirical: real Firefox +
        # Camoufox both get blocked by SpinQuest's GeoComply check
        # ("not in legal jurisdiction"); the user's regular Chrome is
        # allowed on the same IP. Same machine, same network —
        # engine-level fingerprint divergence (most likely WebRTC
        # ICE-candidate handling, where Firefox is privacy-conservative
        # and GeoComply's SDK was expecting Chrome-shape data).
        #
        # ``browser_backend="chrome"`` + ``real_chrome=True`` together
        # mean: launch the system-installed Chrome via patchright
        # rather than patchright's bundled Chromium. The bundled
        # Chromium is closer to vanilla and could plausibly also pass
        # GeoComply, but the user's real Chrome is the empirically
        # verified path. If GeoComply ever escalates beyond what real
        # Chrome can pass, there's nothing more aggressive to fall
        # back to in-process.
        #
        # Switching off Camoufox drops ``geoip=True`` (a Camoufox-only
        # stealth feature), but real Chrome is naturally coherent with
        # the user's real IP — no auto-correlation needed. The
        # ``_grant_geo`` permission grant in ``pre_login`` still
        # applies (it's a context permission, not engine-specific).
        # Profile invalidated by the swap (Firefox profile layout ≠
        # Chrome): re-run ``--setup`` to bootstrap a fresh
        # authenticated profile.
        # browser_backend="camoufox",
        # SpinQuest's lobby + balance hydration is the slowest of the
        # working set (login submit → cookies → navigate → React init →
        # balance fetch chain regularly takes 30-50s). Bumping
        # ``page_wait_timeout`` so the post-login ``wait_for_load_all_safe``
        # actually waits for the load to settle rather than racing it.
        page_wait_timeout=60000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_spinquest_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
