"""
Modo.us Casino Automation Script
================================

Automates login + daily claim on modo.us. Different lineage from the
SLNGApp / RealPlay / Pulsz families — Modo ships its own React app
with stable ``data-testid`` hooks throughout the lobby
(``sc-button`` / ``gc-button`` / ``player-side-nav`` / etc.).

Auth path: Google OAuth. First-time setup:

  python modo.py --google-oauth --setup

Normal headless runs after setup:

  python modo.py --google-oauth [--headless]

Currency display (verified live in the authenticated lobby):

    [data-testid="sc-button"]   — Sweeps Coins
    [data-testid="gc-button"]   — Gold Coins

Both buttons are visible in the header but the value of each only
renders cleanly *after* Modo's auto-popup coin-store dialog
(``?dialog=select-package``) is dismissed — while the dialog is
open the SC button stays empty because the SC display is suppressed
behind the modal backdrop. The post-login callback closes the
dialog via the standard Material-UI ``button[aria-label="close"]``
inside ``.MuiDialog-root``; after that, both currency buttons
populate normally and the framework's ``clean`` step strips
letters / commas / whitespace cleanly.

Daily-claim flow
----------------
Modo has multiple daily features. The simple-claim flow we target
is the **Daily Bonus** card in the left side panel: a
``button.MuiCardActionArea-root`` whose text reads
``"Daily Bonus  Claim now! Ready! 1 day"`` while the bonus is
available. Clicking it surfaces a modal that closes via the same
``button[aria-label="close"]`` pattern as the other Modo popups —
the ``post_login_callback`` popup-stack dismissal will sweep that
up too on the *next* run if it lingers; for the same-run case the
runner registers ``claimed`` once the click succeeds.

Other Modo daily features (Daily Challenge / Daily Lucky Blast /
Modo Daily Hunt) are spin-the-wheel-style minigames or quest
timers — out of scope for the simple-claim flow.

The popup-stack dismissal in ``post_login_callback`` is critical
not just for SC reading: Modo's ``MuiDialog-root`` overlays
interfere with the lobby DOM badly when left up, blocking clicks
on the side-panel Daily Bonus card. With them cleared the lobby
renders cleanly.
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
    make_dismiss_popup_stack,
)
from scrapling_ext import make_casino_automation


# Modo auto-pops a stack of Material-UI dialogs on every fresh
# login (the coin store first, then "Claim your offer!", and
# possibly more over time). They all use the standard MUI close
# icon button at ``button[aria-label="close"]``, so a single
# dismiss-loop walks the stack cleanly.
_dismiss_modo_popups = make_dismiss_popup_stack(
    modal_selector='.MuiDialog-root:not([aria-hidden="true"])',
    close_selector='.MuiDialog-root:not([aria-hidden="true"]) button[aria-label="close"]',
    name="Modo.post-login-stack",
)


def read_modo_balances(page: Page) -> CasinoAccountState:
    """Read both Modo currencies via the click-to-toggle pattern.

    Modo's header has two side-by-side currency buttons —
    ``[data-testid="sc-button"]`` and ``[data-testid="gc-button"]``.
    Only the *active* one renders its numeric value (one digit per
    child span); the inactive one is icon-only. Clicking either
    button toggles which side is active, **but only after the
    auto-popup stack is dismissed** — clicking while the
    coin-store dialog is up just opens that dialog rather than
    toggling. The post-login callback already clears the stack,
    so by the time this parser runs the click is clean.

    Read strategy: for each currency, click its button to make it
    active, then pull the value from ``aria-label``. The button's
    aria-label is the clean numeric value (``"0.33"`` /
    ``"5,834,732"``) when active, or the descriptive label
    (``"Sweepstake Coins"`` / ``"Gold Coins"``) when inactive.
    Reading aria-label sidesteps the digit-per-span text layout
    that would otherwise hand ``float()`` an unparseable
    ``"0\\n.\\n3\\n3"``.
    """
    balances: Dict[str, float] = {}
    pairs = (
        ("SC", '[data-testid="sc-button"]'),
        ("GC", '[data-testid="gc-button"]'),
    )

    for code, selector in pairs:
        try:
            btn = page.locator(selector).first
            if btn.count() == 0:
                log.warning("[Modo] %s button not found at %s", code, selector)
                continue
            try:
                btn.click(delay=gaussian_random_delay(), timeout=5000)
                # 600ms settle: enough for the toggle's transition
                # animation + the React re-render that swaps the
                # active-currency aria-label into place.
                page.wait_for_timeout(600)
            except BrowserError as e:
                log.warning(
                    "[Modo] %s activate click failed: %s", code, e
                )
                continue

            aria = btn.get_attribute("aria-label") or ""
            cleaned = aria.replace(",", "").strip()
            try:
                value = float(cleaned)
            except ValueError:
                # ``aria-label`` is the descriptive label when this
                # side is somehow still inactive (toggle didn't
                # take). Skip rather than recording 0 — the runner
                # treats an absent code as "didn't read" rather
                # than "balance is zero".
                log.warning(
                    "[Modo] %s aria-label %r isn't numeric; "
                    "toggle may not have flipped",
                    code,
                    aria,
                )
                continue

            balances[code] = value
            log.info("[Modo] Found %s balance: %s", code, value)
        except BrowserError as e:
            log.warning("[Modo] %s read failed: %s", code, e)

    return CasinoAccountState(balances=balances)


def create_modo_config() -> CasinoConfig:
    return CasinoConfig(
        name="Modo",
        url="https://modo.us",
        # Modo's auth flow + login URL surface haven't been observed
        # logged-out yet. Defaulting login_url to root and letting the
        # framework's generic HEADER_LOGIN_BUTTON candidates pick the
        # entry point — refine after the first headed run.
        login_url="https://modo.us",
        description="Modo.us Casino Automation",

        login=LoginConfig(
            # Form-login placeholders — only used when ``--google-oauth``
            # is OFF. Modo's account-creation flow is OAuth-driven for
            # this user, so the form path is untested.
            username_selector='input[type="email"]',
            password_selector='input[type="password"]',
            login_submit_selector='button[type="submit"]',
            # Dismiss the stacked store + "Claim your offer!" popups
            # before balance reading.
            post_login_callback=_dismiss_modo_popups,
        ),

        # ``Currency`` entries here are documentation-only — Modo
        # uses ``custom_balance_parser=read_modo_balances`` below
        # (click-to-toggle + aria-label read). The selector lists
        # ride along but the framework's standard text-based read
        # is bypassed.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(name="Sweeps Coins", code="SC", selectors=[]),
                Currency(name="Gold Coins", code="GC", selectors=[]),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),
        custom_balance_parser=read_modo_balances,

        # Daily Bonus card in the left side panel. While the bonus
        # is ready the button text reads "Daily Bonus  Claim now!
        # Ready! ..." — once claimed it switches to a "Next:
        # <countdown>" form that no longer contains "Claim now",
        # so the ``:has-text("Claim now")`` filter naturally
        # registers as ``already_claimed`` after a successful run.
        # ``MuiCardActionArea-root`` is Material-UI's stable
        # clickable-card class (the accompanying ``css-*`` emotion
        # class re-hashes per build and is deliberately not
        # targeted).
        claim_config=SimpleClaimConfig(
            btn_selector='button.MuiCardActionArea-root:has-text("Daily Bonus"):has-text("Claim now")',
        ),
        claim_pattern="simple",

        requires_2fa=False,
        geoip=False,
        solve_cloudflare=False,
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_modo_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
