from casino import (
    CasinoConfig,
    CasinoAccountState,
    GenericClaimConfig,
    MTBClaimConfig,
    SimpleClaimConfig,
    get_credentials,
    google_oauth_login_page_make,
    make_modal_tab_button,
    make_get_casino_account_state,
    make_generic_accept_or_close_modals,
    make_simple_claim_button,
    make_login_action_factory,
    wait_for_load_all_safe,
)
from typing import Callable, Optional, Dict
from playwright.sync_api import Page
from scrapling.fetchers import StealthySession
from scrapling.engines.toolbelt.custom import Response
from scrapling.cli import log

def make_casino_automation(
    config: CasinoConfig,
) -> Callable[[bool, bool, bool, Optional[str], Optional[str]], None]:
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

    # Create login action factory
    login_action_factory = make_login_action_factory(
        username_selector=config.login.username_selector,
        password_selector=config.login.password_selector,
        login_submit_selector=config.login.login_submit_selector,
        totp_code_selector=config.login.totp_code_selector,
        totp_submit_selector=config.login.totp_submit_selector,
        pre_login_form_callback=config.login.pre_login_callback,
        post_login_form_callback=config.login.post_login_callback,
    )

    # Create account state parser (use custom if provided, otherwise use default)
    if config.custom_balance_parser:
        get_account_state = config.custom_balance_parser
    else:
        get_account_state_func = make_get_casino_account_state(config.currency_display)

        def get_account_state(
            page: Page,
        ) -> CasinoAccountState | Dict[str, Optional[float]]:
            return get_account_state_func(page)

    # Create claim bonus action based on pattern
    if config.claim_pattern == "mtb":
        if not isinstance(config.claim_config, MTBClaimConfig):
            raise ValueError(
                "claim_pattern is 'mtb' but claim_config is not MTBClaimConfig"
            )
        claim_bonus_action = make_modal_tab_button(
            modal_selector=config.claim_config.modal_selector,
            tab_selector=config.claim_config.tab_selector,
            btn_selector=config.claim_config.btn_selector,
            close_btn_selector=config.claim_config.close_btn_selector,
        )
    elif config.claim_pattern == "simple":
        if not isinstance(config.claim_config, SimpleClaimConfig):
            raise ValueError(
                "claim_pattern is 'simple' but claim_config is not SimpleClaimConfig"
            )
        claim_bonus_action = make_simple_claim_button(
            btn_selector=config.claim_config.btn_selector,
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
    ):
        """Main function for casino automation.

        Args:
            headless: Run browser in headless mode
            google_oauth: Use Google OAuth (Sign in with Google) instead of
                form credentials. Pair with ``user_data_dir`` to reuse an
                already-authenticated Google session.
            skip_claim: Skip claiming the daily bonus
            proxy: Proxy server to use
            user_data_dir: Path to user data directory for browser session
        """
        # Configure additional browser arguments
        additional_args = {}
        if user_data_dir is not None:
            additional_args["user_data_dir"] = user_data_dir

        # Build the login action: either form credentials or Google OAuth.
        # OAuth still honors the site's pre_login_callback (e.g. clicking
        # the header login button to reach the /login page) before handing
        # off to the Google button-click + redirect flow.
        if google_oauth:
            oauth_login, _ = google_oauth_login_page_make()
            pre_login = config.login.pre_login_callback
            post_login = config.login.post_login_callback

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
            login_action = login_action_factory(username, password, totp_secret)

        def casino_action(page: Page) -> None:
            """Main page action that orchestrates all casino operations."""
            # Step 1: Login
            log.info(f"[{config.name}] Starting login process...")
            login_action(page)
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
        with StealthySession(
            proxy=proxy,
            headless=headless,
            humanize=True,
            load_dom=True,
            google_search=False,
            geoip=config.geoip,
            additional_args=additional_args,
        ) as session:
            log.info(f"[{config.name}] Fetching {config.login_url}...")
            _: Response = session.fetch(
                config.login_url,
                page_action=casino_action,
                wait=config.page_wait_timeout,
                timeout=config.fetch_timeout,
            )
            log.info(f"[{config.name}] Session completed")

    return main
