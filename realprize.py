"""
Real Prize Casino Automation Script
===================================

Automates login + daily claim on realprize.com. RealPlay-operated
sweeps casino — uses an in-house auth platform shared with
LoneStar (the two sites have **identical** login button IDs:
``loggoogleloginbtn``, ``logemailnbtn``, ``logfbloginbtn``,
``poploginbtn``). No SLNGApp / OAuth-PKCE redirect; the login
modal posts directly to the site's own backend.

Login flow (verified via DOM probe at https://realprize.com):

  1. Open the ``#reglogin`` Bootstrap modal. The homepage doesn't
     surface a header LOG IN button on a clean page load — the
     modal is opened via ``$('#reglogin').modal('show')``.
  2. The modal defaults to the *registration* view (three SSO
     buttons + "Already have an account? Login Here" link).
  3. Click the "Login Here" span to switch into the login view
     (still showing the three SSO buttons).
  4. Click ``#logemailnbtn`` (Login with Email) to expose the
     email/password fields.
  5. Fill ``#poplogin_email`` + ``#poplogin_password`` and submit
     via ``#poploginbtn`` (text "LOGIN", class ``reg-default-btn``).

Currency display + daily-claim flow are TBD — RealPrize's
authenticated lobby hasn't been probed yet. First-run output and
follow-up DOM inspection will fill those in.

First-time setup:

  python realprize.py --setup

Normal runs:

  python realprize.py [--headless]
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


def open_login_modal(page: Page) -> None:
    """Open the ``#reglogin`` modal and walk it into the email-login
    sub-view.

    RealPrize's homepage doesn't show a header LOG IN button — the
    auth surface lives entirely inside a Bootstrap modal that has
    to be JS-triggered. The modal multiplexes between several views
    (signup, OAuth chooser, login, etc.) via ``hideit`` class
    toggles; we walk it through ``open → Login Here → Login with
    Email`` so the form fields the framework's login factory expects
    are actually visible by the time it tries to fill them.
    """
    try:
        # The clean way would be ``$('#reglogin').modal('show')``
        # → click "Login Here" → click ``#logemailnbtn``, but
        # Camoufox's evaluation context can't see page-loaded
        # jQuery (Firefox content-script isolation), so calling
        # ``window.jQuery`` from ``page.evaluate`` raises. The
        # Bootstrap modal is mostly CSS-driven, so we DOM-manipulate
        # instead — show the modal root, then unhide the
        # ``regformpop_login`` sub-section (which already contains
        # the email/password fields the framework's login factory
        # targets).
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
              // Unhide the email-login form sub-section. Verified
              // ancestor chain of ``#poplogin_email`` is
              // ``form#loginpopform → .form-container.signup-form2-container
              // → .loginformpop.hideit → .modal-body → .rightpoper
              // → .modal-content → #reglogin``. The only ``hideit``
              // in that chain is ``.loginformpop``.
              const formpop = document.querySelector('.loginformpop');
              if (formpop) formpop.classList.remove('hideit');
              return true;
            })()
            """
        )
        # Wait for the email field to become visible — confirms the
        # DOM state matches what the form factory expects.
        page.wait_for_selector(
            "#poplogin_email", state="visible", timeout=10000
        )
        log.info("[RealPrize] Login modal opened and primed for email entry")
    except Exception as e:
        log.warning("[RealPrize] could not prime login modal: %s", str(e))


def create_realprize_config() -> CasinoConfig:
    return CasinoConfig(
        name="RealPrize",
        url="https://realprize.com",
        login_url="https://realprize.com",
        description="Real Prize Casino Automation",

        login=LoginConfig(
            username_selector="#poplogin_email",
            password_selector="#poplogin_password",
            login_submit_selector="#poploginbtn",
            pre_login_callback=open_login_modal,
        ),

        # Currency display: the lobby keeps both currencies in the
        # DOM at all times — the active one in ``span.gct`` (visible),
        # the inactive one in ``span.gcc.small`` (visually hidden but
        # ``textContent`` reads through). The framework's clean
        # logic strips letters, so raw text "SC 1.38" → "1.38" and
        # "GC247,662.50" → "247662.50".
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

        # Daily claim is a ``.daily_prize_popup`` that auto-shows
        # once after each fresh login — a 7-day streak grid where
        # today's tile carries an active ``div#daily_button``
        # rendering the text "COLLECT" (past days render the same
        # element with text "COLLECTED" — same id, so we filter
        # by exact text). If you miss it (close it, navigate away
        # without clicking COLLECT), the popup is gone for the
        # session and you have to log out + back in. On
        # already-claimed days the active tile flips to "COLLECTED"
        # and the ``text-is("COLLECT")`` selector finds nothing,
        # which the runner registers as ``already_claimed``.
        claim_config=SimpleClaimConfig(
            btn_selector='div.daily_button:text-is("COLLECT")',
        ),
        claim_pattern="simple",

        requires_2fa=False,
        # No GeoComply hooks observed; default browser geo is fine.
        geoip=False,
        # No Cloudflare Turnstile on the login modal observed yet —
        # turn off scrapling's pre-action attempt.
        solve_cloudflare=False,
        # Camoufox for fingerprint consistency with the rest of the
        # working set.
        browser_backend="camoufox",
        page_wait_timeout=5000,
        fetch_timeout=60000,
    )


if __name__ == "__main__":
    config = create_realprize_config()
    main = make_casino_automation(config)

    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()

    main(**vars(args))
