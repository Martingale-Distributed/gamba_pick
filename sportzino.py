"""
Sportzino Automation Script
===========================

Automates login (and daily claim) on sportzino.com. Like Zula, login is a
full-page flow at ``/login`` gated by Cloudflare Turnstile — not a modal —
and the URL carries an OAuth PKCE ``code_challenge`` minted client-side
when the user clicks the homepage login button. Sportzino is the same
codebase family as Zula (same OAuth shape, same Turnstile gating, similar
chunked layout) but the rendered class names are different — this file
mirrors ``zulacasino.py`` with Sportzino-specific selectors swapped in.

Sportzino does NOT use GeoComply or other compliance vendors — only
Cloudflare (Turnstile + insights). No special geolocation handling is
needed; ``geoip`` defaults to False.

Auth path: Google OAuth
-----------------------
This script is set up to use **Google OAuth** (``--google-oauth``).
Form-based email+password login also works, but Sportzino gates it
behind Cloudflare Turnstile — the OAuth path avoids the captcha
entirely.

First-time setup:

  python sportzino.py --google-oauth --setup

``--setup`` launches a non-headless browser, navigates to ``/login``,
then pauses so you can click "Log in with Google", complete 2FA, grant
site consent, etc. Press Enter in the terminal once you're fully signed
in — the browser session persists to ``./profiles/sportzino/`` (the
default when ``--user-data-dir`` isn't specified) and the script exits
without claiming.

Normal runs after setup:

  python sportzino.py --google-oauth [--headless]

The Google consent screen is skipped because the cookies are already in
the profile dir. Override with ``--user-data-dir <path>`` if you want a
different profile location.

Credentials for the form-login path (non-OAuth) come from ``.env`` via
``SPORTZINO_USERNAME`` / ``SPORTZINO_PASSWORD``.
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


def create_sportzino_config() -> CasinoConfig:
    return CasinoConfig(
        name="Sportzino",
        # No www — so url_to_env_prefix yields SPORTZINO (not WWW).
        url="https://sportzino.com",
        login_url="https://sportzino.com",
        description="Sportzino Automation",
        login=LoginConfig(
            # The email field's name attribute is "username" (id="emailAddress").
            username_selector='input[name="username"]',
            password_selector='input[name="password"]',
            # Form-submit CTA. Disabled until Cloudflare Turnstile resolves;
            # Playwright's click() waits for the enabled state.
            login_submit_selector="button.login-form-login-button",
            # ``login_url`` is the homepage; the OAuth PKCE challenge is
            # minted client-side by the header LogIn button's onClick.
            # Navigating directly to /login would lose that, so click
            # the header button to redirect into /login?ReturnUrl=...
            # &code_challenge=... — the canonical entry into the OAuth
            # flow. Fallback class covers the alternate header layout.
            pre_login_click_selector=(
                "button.header-home-v2-login, "
                "button.logged-out-header-login-button"
            ),
        ),
        # Sportzino's logged-in header has a two-button balance switcher
        # (FC + GC always render side-by-side; only one is "active" /
        # full-size at a time).
        #
        # Targeting the DEEPEST ``> span`` is critical: the count-up
        # animation wrapper holds multiple sibling spans (integer part,
        # decimal part, possibly hidden audit values), so reading
        # ``text_content()`` on the parent ``balance-switcher-button-
        # numbers`` concatenates them all and gives garbage like
        # ``43517260.00435173``. The leaf ``> span`` immediately under
        # ``-number-count-up`` (active) or ``-icon-responsive-phone``
        # (inactive) renders just the displayed digits.
        # NOTE: Sweeps Coins is called "Free Coins" (FC) internally,
        #       same as Zula.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=[
                        # Active state — count-up animation deepest span
                        "button.balance-switcher-button-fc span.balance-switcher-button-number-count-up > span",
                        # Inactive state — icon-responsive-phone deepest span
                        "button.balance-switcher-button-fc span.balance-currency-icon-responsive-phone > span > span",
                    ],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=[
                        "button.balance-switcher-button-gc span.balance-switcher-button-number-count-up > span",
                        "button.balance-switcher-button-gc span.balance-currency-icon-responsive-phone > span > span",
                    ],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),
        # The actual claim flow on Sportzino is Store -> COLLECT -> close.
        # The dedicated ``daily-bonus-dialog`` only fires from the toast
        # path; the always-available path goes through the Coin Store
        # popup, where ``button.coin-store-collect-button`` is the real
        # claim CTA (text: "COLLECT"). Pre-open opens the Store popup
        # by clicking the header "Coin Store" button — multiple
        # selectors as fallbacks across desktop / mobile / animated
        # variants.
        claim_config=SimpleClaimConfig(
            btn_selector="button.coin-store-collect-button",
            pre_open_selector=(
                # Desktop header: button labelled "Coin Store"
                'button:has(span.header-auth-button-text:has-text("Coin Store")), '
                'button:has-text("Coin Store"), '
                # Generic store-entry roots (chunk source: coinStoreEntry.root)
                ".coin-store-entry.animated, "
                ".coin-store-entry, "
                # Mobile bottom-nav
                ".bottom-nav-bar-store-button.animated, "
                ".bottom-nav-bar-store-button"
            ),
            # After clicking COLLECT, dismiss whatever confirmation modal
            # appears: either the dedicated bonus-collected popup OR a
            # generic store info dialog. First visible match wins.
            post_claim_close_selector=(
                "button.daily-bonus-collected-popup-close-button, "
                "button.coin-store-info-dialog-close-button, "
                "button.coin-store-popup-close-button"
            ),
        ),
        claim_pattern="simple",
        requires_2fa=False,
        # No compliance-vendor geo gate on Sportzino; default browser geo is fine.
        geoip=False,
        # Turnstile is handled by our own ``wait_for_turnstile`` (active
        # auto-click solver in casino.py), not scrapling's. False here
        # disables scrapling's pre-action attempt that fires too early
        # (500ms wait before the widget has mounted) and adds a noisy
        # ``No Cloudflare challenge found`` log line.
        solve_cloudflare=False,
        # Use Chrome (Patchright stealth Chromium with ``real_chrome=True``
        # below) instead of Camoufox. Sportzino's Turnstile escalates to a
        # *managed* challenge (render=explicit, .login-turnstile-container)
        # when it doesn't trust the fingerprint — Camoufox's Firefox-based
        # signals trigger that escalation. Chrome's fingerprint passes
        # Turnstile's invisible path on this site without interaction; if
        # it ever does escalate, the active solver in ``wait_for_turnstile``
        # clicks the checkbox. Camoufox would also work with the active
        # solver — switch if you need geoip for any reason.
        browser_backend="camoufox",
        #real_chrome=True,
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_sportzino_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
