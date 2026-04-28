from typing import Optional
from playwright.sync_api import (
    Page,
)
from scrapling.fetchers import StealthySession
from scrapling.engines.toolbelt.custom import Response
from scrapling.cli import log
from scrapling_pick import get_credentials

from casino import (
    get_arg_parser,
    CasinoAccountState,
    CurrencyDisplayConfig,
    Currency,
    make_get_casino_account_state,
    make_modal_tab_button,
    make_login_action_factory,
    wait_for_load_all_safe,
)


url = "https://stake.us"
login_url = "https://stake.us/?tab=login&modal=auth"
username_selector = 'input[name="emailOrName"]'
password_selector = 'input[name="password"]'
totp_code_selector = 'input[name="code"]'
login_submit_selector = 'button[type="submit"]'

currency_toggle_selector = 'button[data-testid="coin-toggle"]'
sweeps_coins_selectors = [
    '[data-testid="coin-toggle-currency-sweeps"]',
]
gold_coins_selectors = [
    '[data-testid="coin-toggle-currency-gold"]',
]
# Common selectors for Google One Tap close button
close_selectors = [
    "#close",
    "div#close",
    "[aria-label='Close']",
    "button[aria-label='Close']",
    ".close",
]
wallet_btn_selector = 'button[data-testid="wallet"], button[data-analytics="global-navbar-wallet-button"]'
daily_bonus_btn_selector = 'button[data-testid="dailyBonus"]'
claim_btn_selector = 'button[type="submit"]'
close_btn_selector = 'button[data-testid="modal-close"]'

login_action_factory = make_login_action_factory(
    username_selector=username_selector,
    password_selector=password_selector,
    login_submit_selector=login_submit_selector,
    totp_code_selector=totp_code_selector,
    totp_submit_selector=login_submit_selector,
)

# define currency display configuration
currency_display_config = CurrencyDisplayConfig(
    currencies=[
        Currency(name="Sweeps Coins", code="SC", selectors=sweeps_coins_selectors),
        Currency(name="Gold Coins", code="GC", selectors=gold_coins_selectors),
    ],
    currency_toggle_dropdown_selector=currency_toggle_selector,
    currency_toggle_switch_selector=None,
)
# create the get account state function using the factory
get_casino_account_state = make_get_casino_account_state(
    currency_display_config,
)

claim_bonus_action = make_modal_tab_button(
    modal_selector=wallet_btn_selector,
    tab_selector=daily_bonus_btn_selector,
    btn_selector=claim_btn_selector,
    close_btn_selector=close_btn_selector,
)


def main(
    proxy: Optional[str],
    headless: Optional[bool] = False,
    google_oauth: Optional[bool] = False,
    skip_claim: Optional[bool] = False,
    user_data_dir: Optional[str] = None,
    setup: bool = False,
):
    # setup is accepted for argparse compatibility; stake_us uses form
    # credentials and doesn't need the interactive OAuth bootstrap.
    _ = setup
    headless = headless if headless is not None else False
    username, password, totp_secret = get_credentials("https://stake.us")
    action_args = {
        "username": username,
        "password": password,
        "totp_secret": totp_secret,
    }
    additional_args = {}
    if user_data_dir is not None:
        additional_args["user_data_dir"] = user_data_dir

    login_action = login_action_factory(**action_args)

    def casino_action(page: Page) -> None:
        # Perform login
        login_action(page)
        wait_for_load_all_safe(page)

        # Get account state
        account_state: CasinoAccountState = get_casino_account_state(page)
        log.info("Account State: %s", account_state)

        # Claim daily bonus
        if not skip_claim:
            claim_bonus_action(page)
        else:
            # Canonical line for runner.parse_outcome — keeps stake_us
            # (older-style script, doesn't use make_casino_automation)
            # consistent with the framework's vocabulary.
            log.info("[StakeUS] Skipping daily bonus claim (--skip-claim flag set)")
        wait_for_load_all_safe(page)

        # Canonical DONE marker the runner uses to tell that the
        # script reached completion. Emitted even if scrapling's
        # post-action teardown later crashes with TargetClosedError.
        log.info("[StakeUS] Casino action completed successfully")

    with StealthySession(
        proxy=proxy,
        headless=headless,
        humanize=True,
        load_dom=True,
        google_search=False,
        additional_args=additional_args,
    ) as session:
        _: Response = session.fetch(
            login_url,
            page_action=casino_action,
            wait=5000,
        )


if __name__ == "__main__":
    parser = get_arg_parser(description="Stake.us Casino Automation")
    args = parser.parse_args()
    main(**vars(args))
