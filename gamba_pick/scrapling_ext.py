from gamba_pick.casino import (
    BrowserError,
    CasinoConfig,
    CasinoAccountState,
    GenericClaimConfig,
    MTBClaimConfig,
    SimpleClaimConfig,
    gaussian_random_delay,
    get_credentials,
    google_oauth_login_page_make,
    make_modal_tab_button,
    make_get_casino_account_state,
    make_generic_accept_or_close_modals,
    make_simple_claim_button,
    make_login_action_factory,
    wait_for_load_all_safe,
    wait_for_turnstile,
)
from pathlib import Path
from typing import Callable, Optional, Dict
from playwright.sync_api import Page

from gamba_pick.selectors_generic import HEADER_LOGIN_BUTTON as _GENERIC_HEADER_LOGIN_BUTTON
from scrapling.fetchers import DynamicSession, StealthySession
from scrapling.engines.toolbelt.custom import Response
from scrapling.cli import log


# Setup mode gives the human time to click through consent screens,
# possibly handle 2FA, etc. The normal 60s fetch timeout isn't enough.
SETUP_FETCH_TIMEOUT_MS = 600_000  # 10 minutes


def _default_oauth_profile_dir(name: str) -> str:
    """Derive a per-site profile dir under ``./profiles/<name>``.

    Keeps OAuth sessions isolated per site (so a shared Google profile
    can't be used to correlate activity across multiple sweepstakes
    casinos, which is exactly what compliance systems look for).
    """
    slug = "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()
    path = Path("profiles") / slug
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def make_pre_login_click(selector: str) -> Callable[[Page], None]:
    """Synthesize a pre-login callback from a single CSS selector.

    Sites where ``login_url`` points at the homepage need a click on a
    header login button to mint the OAuth PKCE challenge and navigate
    to the real ``/login`` page. This helper turns the bare selector
    from ``LoginConfig.pre_login_click_selector`` into the same shape
    of callback users would otherwise hand-roll (cf. Zula's
    ``click_header_login``).

    Public so external configs can compose this with other primitives
    (e.g. ``casino.make_dismiss_popup`` for a cookie-consent banner)
    inside their own ``pre_login_callback``, without giving up the
    framework's standard click-and-wait-for-/login behavior.

    The click uses ``no_wait_after=True`` — Playwright's default
    post-click nav-wait creates a driver-side promise that orphans on
    long redirects and crashes the Node side via its
    unhandled-rejection handler. We observe the navigation explicitly
    via ``wait_for_url``.
    """

    def click_pre_login(page: Page) -> None:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0 or not loc.is_visible():
                log.info(
                    "Pre-login trigger '%s' not visible; skipping (already on /login?)",
                    selector,
                )
                return
            loc.click(
                delay=gaussian_random_delay(),
                timeout=10000,
                no_wait_after=True,
            )
            log.info("Clicked pre-login trigger: %s", selector)
            try:
                page.wait_for_url("**/login*", timeout=15000)
                log.info("Reached /login page (url=%s)", page.url)
            except BrowserError:
                log.info(
                    "Timed out waiting for /login navigation; current url=%s",
                    page.url,
                )
        except BrowserError as e:
            log.warning("Pre-login click failed: %s", str(e))

    return click_pre_login

def make_casino_automation(
    config: CasinoConfig,
) -> Callable[..., None]:
    """Factory function that creates a complete casino automation main function.

    This function takes a CasinoConfig and returns a ready-to-use main() function
    that can be called with standard CLI arguments.

    Args:
        config: CasinoConfig object with all casino-specific parameters

    Returns:
        A main() function that accepts: headless, google_oauth, skip_claim, proxy, user_data_dir

    Example:
        >>> config = CasinoConfig(
        ...     name="MyCasino",
        ...     url="https://mycasino.com",
        ...     login_url="https://mycasino.com/login",
        ...     login=LoginConfig(...),
        ...     currency_display=CurrencyDisplayConfig(...),
        ...     claim_config=MTBClaimConfig(...),
        ... )
        >>> main = make_casino_automation(config)
        >>> main(headless=True, skip_claim=False, proxy=None, user_data_dir=None)
    """

    # NOTE: ``login_action_factory`` is built later, inside ``main()``,
    # after the per-call ``pre_login`` is resolved (it may be the raw
    # ``pre_login_callback``, a synthesized click on
    # ``pre_login_click_selector``, or the generic
    # ``HEADER_LOGIN_BUTTON`` fallback). Building it up here would
    # capture the raw callback and skip the latter two branches in
    # the form-login path.

    # Create account state parser (use custom if provided, otherwise use default)
    if config.custom_balance_parser:
        get_account_state = config.custom_balance_parser
    else:
        get_account_state_func = make_get_casino_account_state(config.currency_display)

        def get_account_state(
            page: Page,
        ) -> CasinoAccountState | Dict[str, Optional[float]]:
            return get_account_state_func(page)

    # Create claim bonus action: custom callable wins if provided;
    # otherwise dispatch on ``claim_pattern`` against ``claim_config``.
    if config.custom_claim_action is not None:
        claim_bonus_action = config.custom_claim_action
    elif config.claim_config is None:
        raise ValueError(
            "CasinoConfig must provide either ``claim_config`` or "
            "``custom_claim_action``"
        )
    elif config.claim_pattern == "mtb":
        if not isinstance(config.claim_config, MTBClaimConfig):
            raise ValueError(
                "claim_pattern is 'mtb' but claim_config is not MTBClaimConfig"
            )
        claim_bonus_action = make_modal_tab_button(
            modal_selector=config.claim_config.modal_selector,
            tab_selector=config.claim_config.tab_selector,
            btn_selector=config.claim_config.btn_selector,
            close_btn_selector=config.claim_config.close_btn_selector,
            btn_visibility_timeout_ms=config.claim_config.btn_visibility_timeout_ms,
            already_claimed_selector=config.claim_config.already_claimed_selector,
            pre_claim_settle_ms=config.claim_config.pre_claim_settle_ms,
        )
    elif config.claim_pattern == "simple":
        if not isinstance(config.claim_config, SimpleClaimConfig):
            raise ValueError(
                "claim_pattern is 'simple' but claim_config is not SimpleClaimConfig"
            )
        claim_bonus_action = make_simple_claim_button(
            btn_selector=config.claim_config.btn_selector,
            pre_open_selector=config.claim_config.pre_open_selector,
            post_claim_close_selector=config.claim_config.post_claim_close_selector,
        )
    else:  # generic
        if not isinstance(config.claim_config, GenericClaimConfig):
            raise ValueError(
                "claim_pattern is 'generic' but claim_config is not GenericClaimConfig"
            )
        claim_bonus_action = make_generic_accept_or_close_modals(
            main_enabled_selector=config.claim_config.main_enabled_selector,
            modal_selector=config.claim_config.modal_selector,
            close_modal_selector=config.claim_config.close_modal_selector,
        )

    def main(
        headless: bool = False,
        google_oauth: bool = False,
        skip_claim: bool = False,
        proxy: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        setup: bool = False,
    ):
        """Main function for casino automation.

        Args:
            headless: Run browser in headless mode
            google_oauth: Use Google OAuth (Sign in with Google) instead of
                form credentials. Pair with ``user_data_dir`` to reuse an
                already-authenticated Google session.
            skip_claim: Skip claiming the daily bonus
            proxy: Proxy server to use
            user_data_dir: Path to user data directory for browser session.
                When ``google_oauth`` is set and this is None, defaults to
                ``./profiles/<sitename>`` so sessions persist between runs.
            setup: Interactive setup mode. Launches non-headless, runs
                ``pre_login_callback`` to reach the login page, then pauses
                waiting for the user to complete Google OAuth (or any
                first-time auth step) by hand. Session is saved into
                ``user_data_dir`` and the script exits without claiming.
        """
        # Default a per-site OAuth profile so re-runs keep the Google session.
        if (google_oauth or setup) and user_data_dir is None:
            user_data_dir = _default_oauth_profile_dir(config.name)
            log.info(
                f"[{config.name}] No --user-data-dir provided; using default %s",
                user_data_dir,
            )

        # Setup implies interactive: headless makes no sense here.
        if setup and headless:
            log.warning(
                f"[{config.name}] --setup implies non-headless; ignoring --headless"
            )
            headless = False

        # Both StealthySession and DynamicSession expose user_data_dir as a
        # top-level constructor arg; pass "" when not set to mean "ephemeral".
        session_user_data_dir = user_data_dir or ""
        # Currently unused but reserved for future launch-time overrides
        # (flags that need to reach Playwright/Camoufox directly).
        additional_args: Dict = {}

        # Build the login action: either form credentials or Google OAuth.
        # Pre-login resolution, in priority order (each beats the next):
        #   1. ``pre_login_callback`` — caller hand-rolled a function;
        #      use it verbatim.
        #   2. ``pre_login_click_selector`` — caller named a specific
        #      selector for the header login button; synthesize a
        #      click-and-wait-for-/login handler from it.
        #   3. **Generic fallback** — neither was set; fall back to the
        #      shared ``HEADER_LOGIN_BUTTON`` candidate list from
        #      selectors_generic. Logged at INFO so the implicit choice
        #      is auditable. The synthesized click is a graceful no-op
        #      when no candidate is visible (e.g. the site's login_url
        #      already lands on /login), so this is always safe to try.
        pre_login = config.login.pre_login_callback
        if pre_login is None:
            if config.login.pre_login_click_selector:
                pre_login = make_pre_login_click(
                    config.login.pre_login_click_selector
                )
            else:
                log.info(
                    f"[{config.name}] No pre_login set; falling back to "
                    "generic HEADER_LOGIN_BUTTON candidates"
                )
                pre_login = make_pre_login_click(
                    ", ".join(_GENERIC_HEADER_LOGIN_BUTTON)
                )
        post_login = config.login.post_login_callback

        if setup:
            # Setup mode runs pre_login to get us to the login page, then
            # pauses so the user can click "Sign in with Google", complete
            # any 2FA, grant site consent, etc. We don't call oauth_login
            # here — the human is driving. Browser persistence writes cookies
            # to user_data_dir as they arrive, so the session will be
            # available on subsequent headless runs.
            def login_action(page: Page) -> None:
                if pre_login is not None:
                    pre_login(page)
                # Detect and wait out any Turnstile challenge on the
                # login page so the terminal reflects its state. Long
                # timeout (5 min) since a visible challenge may need
                # human interaction — clicking Google before Turnstile
                # is green triggers a hard reject on most sites.
                wait_for_turnstile(page, timeout=300_000)
                log.info("=" * 70)
                log.info(
                    f"[{config.name}] Browser is at the login page. "
                    "Complete sign-in manually (Google OAuth, 2FA, site "
                    "consent, whatever's needed)."
                )
                log.info(
                    f"[{config.name}] When you're fully authenticated on "
                    "the site, come back here and press Enter to save the "
                    "session and exit."
                )
                log.info("=" * 70)
                try:
                    input("Press Enter when finished... ")
                except (EOFError, KeyboardInterrupt):
                    log.warning(f"[{config.name}] Setup cancelled by user")
                    return
                # Brief settle window for any in-flight cookie writes.
                page.wait_for_timeout(1500)
                log.info(
                    f"[{config.name}] Setup complete — session written to %s",
                    user_data_dir,
                )
        elif google_oauth:
            oauth_login, _ = google_oauth_login_page_make(
                button_selectors=config.login.google_oauth_btn_selectors,
            )

            def login_action(page: Page) -> None:
                if pre_login is not None:
                    pre_login(page)
                oauth_login(page)
                if post_login is not None:
                    post_login(page)
        else:
            username, password, totp_secret = get_credentials(
                config.url, twofa=config.requires_2fa
            )
            # Build the form-login factory NOW (rather than at the top
            # of make_casino_automation) so the resolved ``pre_login``
            # — generic-fallback, click-selector-synthesized, or raw
            # callback — gets threaded through.
            login_action_factory = make_login_action_factory(
                username_selector=config.login.username_selector,
                password_selector=config.login.password_selector,
                login_submit_selector=config.login.login_submit_selector,
                totp_code_selector=config.login.totp_code_selector,
                totp_submit_selector=config.login.totp_submit_selector,
                pre_login_form_callback=pre_login,
                post_login_form_callback=post_login,
            )
            login_action = login_action_factory(username, password, totp_secret)

        def casino_action(page: Page) -> None:
            """Main page action that orchestrates all casino operations."""
            # Step 1: Login
            log.info(f"[{config.name}] Starting login process...")
            login_action(page)
            if setup:
                # In setup mode the login action blocks on user input; once
                # it returns we just exit so the session writes and the
                # browser closes cleanly. Skip balance/claim entirely.
                return
            wait_for_load_all_safe(page)
            log.info(f"[{config.name}] Login completed successfully")

            # Step 2: Get account state
            log.info(f"[{config.name}] Reading account balances...")
            account_state = get_account_state(page)
            log.info(f"[{config.name}] Account State: %s", account_state)

            # Step 3: Claim bonus (unless skipped)
            if not skip_claim:
                log.info(f"[{config.name}] Attempting to claim daily bonus...")
                try:
                    result = claim_bonus_action(page)
                    if config.claim_pattern in ("generic", "simple"):
                        if result:
                            log.info(
                                f"[{config.name}] Daily bonus claimed successfully"
                            )
                        else:
                            log.info(
                                f"[{config.name}] No daily bonus available to claim"
                            )
                    else:
                        log.info(f"[{config.name}] Bonus claim action completed")
                except Exception as e:
                    log.error(f"[{config.name}] Error during bonus claim: %s", str(e))
            else:
                log.info(
                    f"[{config.name}] Skipping daily bonus claim (--skip-claim flag set)"
                )

            wait_for_load_all_safe(page, timeout=config.page_wait_timeout)

            # Step 4: Additional actions (if any)
            if config.additional_actions:
                log.info(
                    f"[{config.name}] Executing {len(config.additional_actions)} additional action(s)..."
                )
                for i, action in enumerate(config.additional_actions, 1):
                    try:
                        log.info(
                            f"[{config.name}] Running additional action {i}/{len(config.additional_actions)}"
                        )
                        action(page)
                        wait_for_load_all_safe(page)
                    except Exception as e:
                        log.error(
                            f"[{config.name}] Error in additional action {i}: %s",
                            str(e),
                        )

            log.info(f"[{config.name}] Casino action completed successfully")

        # Execute the casino action in a stealthy browser session
        if config.browser_backend == "chrome":
            # Patchright-based stealth Chromium (or real Chrome via
            # ``real_chrome=True``). No geoip / solve_cloudflare knobs on
            # this backend, so those CasinoConfig fields are ignored.
            session_cm = DynamicSession(
                proxy=proxy,
                headless=headless,
                load_dom=True,
                google_search=False,
                stealth=True,
                real_chrome=config.real_chrome,
                user_data_dir=session_user_data_dir,
                additional_args=additional_args,
            )
        else:
            session_cm = StealthySession(
                proxy=proxy,
                headless=headless,
                humanize=True,
                load_dom=True,
                google_search=False,
                geoip=config.geoip,
                solve_cloudflare=config.solve_cloudflare,
                user_data_dir=session_user_data_dir,
                additional_args=additional_args,
            )
        with session_cm as session:
            log.info(f"[{config.name}] Fetching {config.login_url}...")
            _: Response = session.fetch(
                config.login_url,
                page_action=casino_action,
                wait=config.page_wait_timeout,
                timeout=SETUP_FETCH_TIMEOUT_MS if setup else config.fetch_timeout,
            )
            log.info(f"[{config.name}] Session completed")

    return main
