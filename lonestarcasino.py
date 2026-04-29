"""
LoneStar Casino Automation Script
=================================

Automates login + daily claim on lonestarcasino.com. RealPlay-operated
sweeps casino, sister site to RealPrize — both share an in-house auth
platform with **identical** DOM signatures: same ``#reglogin`` modal,
same ``.loginformpop`` sub-section, same ``#poplogin_email`` /
``#poplogin_password`` / ``#poploginbtn`` form fields, same
``#coinswitch`` currency display, same ``.daily_prize_popup`` daily
streak grid with ``div#daily_button`` "COLLECT" button.

This config is structurally a near-clone of ``realprize.py``. The only
difference is the host (``lonestarcasino.com`` vs ``realprize.com``)
and the credentials (``LONESTARCASINO_USERNAME`` /
``LONESTARCASINO_PASSWORD`` vs ``REALPRIZE_*``). If a future RealPlay
site shows up, the duplicated logic here is the candidate to extract
into a shared template.

Login flow (same as RealPrize):

  1. Open the ``#reglogin`` Bootstrap modal via DOM manipulation
     (Camoufox can't see page-loaded jQuery, so we strip the
     ``hideit`` class off ``.loginformpop`` directly).
  2. Fill ``#poplogin_email`` + ``#poplogin_password``.
  3. Submit via ``#poploginbtn``.

Daily-claim flow:

  * After login, ``.daily_prize_popup`` auto-shows. Click the
    visible ``div#daily_button:text-is("COLLECT")`` to claim.
  * If you miss it (close it, navigate away), the popup is gone
    for the session and you have to log out + back in. Already-
    claimed days flip the visible tile to "COLLECTED" and the
    text-is selector finds nothing → runner reports
    ``already_claimed``.

First-time setup:

  python lonestarcasino.py --setup

Normal runs:

  python lonestarcasino.py [--headless]
"""

from playwright.sync_api import Page

from casino import (
    CasinoConfig,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
    SimpleClaimConfig,
    get_arg_parser,
    log,
)
from scrapling_ext import make_casino_automation


def open_login_modal(page: Page) -> None:
    """Open the ``#reglogin`` modal and expose the email-login fields.

    Same approach as ``realprize.py`` — Camoufox's evaluation context
    can't see page-loaded jQuery under Firefox's content-script
    isolation, so we DOM-manipulate the modal visibility directly
    rather than calling ``$('#reglogin').modal('show')``. Removing
    the ``hideit`` class from ``.loginformpop`` is enough to expose
    the email-login fields the framework's login factory expects.
    """
    try:
        page.evaluate(
            """
            (() => {
              const modal = document.getElementById('reglogin');
              if (!modal) return false;
              modal.classList.add('show');
              modal.style.display = 'block';
              modal.removeAttribute('aria-hidden');
              modal.setAttribute('aria-modal', 'true');
              document.body.classList.add('modal-open');
              const formpop = document.querySelector('.loginformpop');
              if (formpop) formpop.classList.remove('hideit');
              return true;
            })()
            """
        )
        page.wait_for_selector(
            "#poplogin_email", state="visible", timeout=10000
        )
        log.info("[LoneStar] Login modal opened and primed for email entry")
    except Exception as e:
        log.warning("[LoneStar] could not prime login modal: %s", str(e))


def create_lonestarcasino_config() -> CasinoConfig:
    return CasinoConfig(
        name="LoneStar",
        # Apex for the env-prefix derivation (LONESTARCASINO_USERNAME).
        url="https://lonestarcasino.com",
        # The site redirects apex → www; using the post-redirect host
        # for the actual fetch sidesteps scrapling's Camoufox
        # redirect-response hang.
        login_url="https://www.lonestarcasino.com",
        description="LoneStar Casino Automation",

        login=LoginConfig(
            username_selector="#poplogin_email",
            password_selector="#poplogin_password",
            login_submit_selector="#poploginbtn",
            pre_login_callback=open_login_modal,
        ),

        # Same RealPlay coinswitch as RealPrize — active currency in
        # ``span.gct`` (visible), inactive in ``span.gcc.small``
        # (hidden but ``textContent`` reads through). Framework
        # cleans the currency-letter prefix.
        currency_display=CurrencyDisplayConfig(
            currencies=[
                Currency(
                    name="Sweeps Coins",
                    code="SC",
                    selectors=["#coinswitch span.gct"],
                ),
                Currency(
                    name="Gold Coins",
                    code="GC",
                    selectors=["#coinswitch span.gcc"],
                ),
            ],
            currency_toggle_dropdown_selector=None,
            currency_toggle_switch_selector=None,
        ),

        # Same ``.daily_prize_popup`` grid pattern as RealPrize. The
        # 7-day streak grid renders a ``div.daily_button`` element
        # on each day-tile; today's tile carries text "COLLECT",
        # past days carry "COLLECTED" — exact-text match picks the
        # right one.
        claim_config=SimpleClaimConfig(
            btn_selector='div.daily_button:text-is("COLLECT")',
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
    config = create_lonestarcasino_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
