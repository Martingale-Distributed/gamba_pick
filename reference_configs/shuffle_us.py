"""
Shuffle.us Casino Automation Script
===================================

Automates login + daily bonus claim on shuffle.us. The daily bonus lives
inside the wallet modal under a "Daily Bonus" tab — classic MTB chain:
wallet icon → "Daily Bonus" tab → "Claim".

Login surface (verified via DOM probe at the login modal):
- ``input[name="username"]`` (data-testid="username") + ``input[name="password"]``
- ``button[type="submit"]`` with text "Login"
- Google OAuth lives in the same modal as a button containing an
  ``img[alt="Google"]``.
- The login modal mounts directly when the URL has
  ``?md-tab=login&modal=AUTH`` — same trick stake_us uses for ``?tab=login``,
  saves a click and avoids a pre_login callback.

Currency display (header buttons in the logged-in lobby):
- ``[data-testid="GC"]`` — Gold Coins (e.g. ``304,482.82``).
- ``[data-testid="SC"]`` — Sweeps Coins (e.g. ``0.24``).
Both are rendered concurrently — no per-currency activator dance needed.

Daily-bonus chain (verified live in the lobby):
- modal_selector → ``button:has(img[alt="wallet"])`` — the wallet icon
  next to the SC balance in the header. Opens the wallet modal at the
  ``Purchase Coins`` tab.
- tab_selector → ``Daily Bonus`` tab inside the modal. CSS-module
  prefix ``ModalTabOption_root`` is the family hook; pinning to
  ``:has-text("Daily Bonus")`` disambiguates from sibling tabs
  (Buy Coins, Redeem, Top Up).
- btn_selector → scoped ``Claim`` button in the daily-bonus tab body.
  Scope to ``[class*="ModalContent_show"]`` so the lobby's rakeback
  ``Claim`` tile (same text on the same page) doesn't match.
- close_btn_selector → ``button[aria-label="Close modal"]`` (the X on
  the modal header).

Credentials come from picks.env via SHUFFLE_US_USERNAME / SHUFFLE_US_PASSWORD
(prefix derived from the ``shuffle.us`` domain by url_to_env_prefix).

Usage
-----
python shuffle_us.py [--headless] [--google-oauth] [--skip-claim]
"""

from gamba_pick.casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    MTBClaimConfig,
    close_selectors,
    get_arg_parser,
    make_handle_google_one_tap_popup,
)
from gamba_pick.scrapling_ext import make_casino_automation


def create_shuffle_us_config() -> CasinoConfig:
    return CasinoConfig(
        name="Shuffle.us",
        url="https://shuffle.us",
        # ``md-tab=login&modal=AUTH`` in the query string mounts the
        # login modal on first paint. Saves the header-Login click
        # and avoids a pre_login callback (same shape as stake_us's
        # ``?tab=login&modal=auth``).
        login_url="https://shuffle.us/?md-tab=login&modal=AUTH",
        description="Shuffle.us Casino Automation",

        login=LoginConfig(
            username_selector='input[name="username"]',
            password_selector='input[name="password"]',
            login_submit_selector='button[type="submit"]',
            # 2FA challenge (post-submit, when the trust cookie isn't
            # current). ``input[autocomplete="one-time-code"]`` is the
            # semantic stable selector — the visible class
            # ``OTPInput_bubbleInput__zo31x`` carries a CSS-module hash
            # that churns across builds. ``button[form="otp-form"]``
            # uses the ``form`` linkage attribute (same pattern Shuffle
            # uses across their submit buttons), avoiding the hashed
            # ``ButtonVariants_*`` classes.
            totp_code_selector='input[autocomplete="one-time-code"]',
            totp_submit_selector='button[form="otp-form"]',
            # The login modal hosts multiple SSO providers in an icon
            # row below the form — Google's button is identified by
            # its ``img[alt="Google"]`` child. Pin to that to avoid
            # mis-matching the sibling provider buttons.
            google_oauth_btn_selectors=(
                'button:has(img[alt="Google"])',
            ),
            # Two reasons to set this explicitly rather than letting
            # the framework synthesize a generic header-LOGIN click:
            # (1) Google One Tap mounts an iframe over the page on
            #     load; ``make_login_action_factory`` defaults its
            #     ``pre_login_form_callback`` to dismiss it, but
            #     ``make_casino_automation`` overwrites that default
            #     with the resolved ``pre_login`` (scrapling_ext.py),
            #     so the One Tap handler has to be re-supplied here
            #     or it never runs.
            # (2) The login modal is mounted directly via the
            #     ``?md-tab=login&modal=AUTH`` query string in
            #     ``login_url`` — no header button click required.
            #     The framework's generic-fallback header-LOGIN
            #     click would otherwise spend 10s timing out behind
            #     the already-open modal overlay.
            pre_login_callback=make_handle_google_one_tap_popup(close_selectors),
        ),

        # Both balances render concurrently in the header — no toggle
        # required. The ``data-testid`` attrs are stable production
        # hooks (the site uses them throughout, including for nav
        # buttons like LOBBY/SLOTS/etc.).
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=['[data-testid="SC"]'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=['[data-testid="GC"]'],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        claim_config=MTBClaimConfig(
            # ``#wallet-btn`` is the stable id on the header wallet
            # button. The previous selector ``button:has(img[alt="wallet"])``
            # was ambiguous after a UI revamp added a mobile-menu
            # ``ExpandMenuElement_menuItem`` that also contains the
            # wallet image — Playwright's strict mode (which our
            # ``safe_click`` enforces) rejects multi-match locators.
            modal_selector='#wallet-btn',
            tab_selector='button[class*="ModalTabOption_root"]:has-text("Daily Bonus")',
            btn_selector='[class*="ModalContent_show"] button:has-text("Claim")',
            close_btn_selector='button[aria-label="Close modal"]',
            # When already claimed, shuffle replaces the Claim button
            # with a countdown ``<p class="TimeRemain_timeRemain__hash">5h 58m
            # 32s until claim</p>``. Match on the CSS-module name
            # (the ``__hash`` suffix churns across builds).
            already_claimed_selector='p[class*="TimeRemain_timeRemain"]',
        ),
        claim_pattern="mtb",

        # 2FA enabled on this account — TOTP secret read from
        # ``SHUFFLE_US_2FA`` in picks.env, filled into the OTP input
        # via the selectors above when the post-login challenge fires.
        # The challenge appears intermittently (only when the trust
        # cookie isn't current), so the framework's TOTP block is a
        # no-op when the input never mounts.
        requires_2fa=True,
        geoip=False,
        solve_cloudflare=False,
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_shuffle_us_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
