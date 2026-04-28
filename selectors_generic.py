"""Generic selector candidate lists shared across casino sites.

Most sweepstakes sites in this codebase are variants of the same template
(Stake-family / Mediumrare-derived). They share the same auth shape,
the same Cloudflare gating, similar header layouts, and similar coin-store
/ daily-bonus flows — so each "what to click" point in the framework can
fall back to a candidate list that covers the variants we've actually
seen, when a site config doesn't override it explicitly.

Runtime philosophy: **fail-loud-when-explicit, fall-back-when-unset.**

  - If a site config provides a selector, use *only* that. Failing
    selectors should be loud (visible site change) rather than silently
    drifting onto a generic that may match the wrong element.
  - If a site config leaves a field unset / ``None``, the action
    factories fall back to the candidate list here.

This is a shared registry, not a public API. Lists may grow as we add
sites; ordering matters — earlier entries are tried first and should be
the most specific patterns we've seen. Site-specific classes go *before*
the looser text-based fallbacks.
"""


# Header LogIn button on the public homepage. Clicked by the OAuth-PKCE
# pre-login navigator to redirect from ``/`` to ``/login?ReturnUrl=...&
# code_challenge=...`` — direct navigation to ``/login`` would lose the
# client-minted PKCE nonce.
HEADER_LOGIN_BUTTON: tuple[str, ...] = (
    "button.header-home-v2-login",                # Sportzino V2 layout
    "button.logged-out-header-login-button",      # Sportzino chunk-source canonical
    "button.unauthorized-header-login-btn",       # Zula
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign in")',
)


# "Sign in / Log in with Google" SSO button on the /login page. Clicked
# after Turnstile resolves; opens an OAuth popup or same-tab redirect.
GOOGLE_OAUTH_BUTTON: tuple[str, ...] = (
    "button.sso-button--gg",                      # Zula (Google modifier)
    "button.sso-button",                          # Sportzino (plain)
    'button:has-text("Sign in with Google")',
    'button:has-text("Log in with Google")',
    'button:has-text("Continue with Google")',
    'button:has-text("Google")',
    'a:has-text("Google")',
    "[class*='google'][class*='login']",
    "[id*='google'][id*='login']",
)


# Cloudflare Turnstile widget container. The active-click solver clicks
# the first visible match at offset ``TURNSTILE_CHECKBOX_OFFSET`` from
# its bounding-box origin. CF's stock id varies between ``cf_turnstile``
# (underscore, scrapling default) and ``cf-turnstile`` (hyphen, more
# common in the wild); both are listed. Site-specific wrappers come
# after the canonical ones so pages with both prefer the real CF div.
TURNSTILE_WIDGET: tuple[str, ...] = (
    "div.cf-turnstile",
    "div#cf_turnstile",
    "div#cf-turnstile",
    "div.login-turnstile",                        # Sportzino / Zula wrapper
    ".login-form-content-turnstile",              # Other Stake-family layouts
    "[class*='turnstile-container']",
    "iframe[src*='challenges.cloudflare.com']",
)


# Pixel offset from the widget's bounding-box origin to the visible
# checkbox. The stock "compact" Turnstile widget is ~300x65px with the
# checkbox in the upper left at roughly (26, 25). This is the same
# offset scrapling's solver uses for /login pages and works across the
# canonical CF div and the Stake-family wrappers we've seen so far.
TURNSTILE_CHECKBOX_OFFSET: tuple[int, int] = (26, 25)


# Generic close-button candidates for dismissing arbitrary modals
# (welcome popups, daily-bonus dialogs without a stable close class,
# Google One Tap, etc.). Tried in order; first visible match wins.
# Used as the default fallback list for ``make_dismiss_popup`` and
# ``make_handle_google_one_tap_popup``.
#
# ``close-popup-button`` is the shared Stake-family / Mediumrare close
# class — same one MTBClaimConfig.close_btn_selector uses. Lives at
# the .modal-dialog level inside the .modal.show outer shell, rendered
# as "+" text rotated to × via CSS. Kept first among site-pattern
# entries because it's the most reliable cross-site match we've seen.
MODAL_CLOSE_BUTTON: tuple[str, ...] = (
    "button.close-popup-button",                  # Stake-family (FortuneWins, Zula, Sportzino store)
    "button.transparent-close-popup-button",      # Same button, alt class wrapper variant
    "#close",
    "div#close",
    "[aria-label='Close']",
    "[aria-label='close']",
    "button[aria-label='Close']",
    "button[aria-label='close']",
    ".close",
    ".modal-close",
    "[data-testid='modal-close']",
)
