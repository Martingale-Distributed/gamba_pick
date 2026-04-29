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
Modo has multiple daily features:

    * ``Daily Bonus`` — a "FREE 25 SC" CTA on the lobby that claims
      the daily SC drop in one click. The 25 SC value escalates with
      streak length; the selector matches the ``FREE <N> SC`` pattern
      via ``:text-matches`` so it survives the value change.
    * ``Daily Challenge`` — a quest-style timer; not the simple
      claim we're targeting here.
    * ``Daily Lucky Blast`` / ``Modo Daily Hunt`` — spin-the-wheel-
      style minigames; out of scope for the simple-claim flow.

This config targets the Daily Bonus CTA only.
"""

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    SimpleClaimConfig,
    get_arg_parser,
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

        # Modo's data-testid hooks — stable across builds. No
        # ``activate_selector`` because clicking either button opens
        # the coin-store dialog rather than toggling active state
        # (see module docstring). We just read whichever side is
        # currently active; the inactive side returns empty and
        # falls through to 0.0.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=['[data-testid="sc-button"]'],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=['[data-testid="gc-button"]'],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # Daily Bonus: "FREE <N> SC" button on the lobby. The N
        # escalates with streak length, so we match the pattern via
        # Playwright's ``:text-matches`` (regex literal, no surrounding
        # word-boundary needed since the button text is the entire
        # CTA). Already-claimed days surface a different button
        # state (no FREE prefix) and the runner registers
        # ``already_claimed`` cleanly.
        claim_config=SimpleClaimConfig(
            btn_selector='button:text-matches("FREE \\d+ SC")',
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
