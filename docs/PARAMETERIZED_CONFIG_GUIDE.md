# Parameterized Casino Configuration System

This guide explains how to use the new parameterized configuration system for creating casino automation scripts. This system eliminates manual placeholder replacement and provides a clean, type-safe way to configure casino automations.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Configuration Classes](#configuration-classes)
4. [Step-by-Step Guide](#step-by-step-guide)
5. [Real-World Examples](#real-world-examples)
6. [Migration Guide](#migration-guide)
7. [API Reference](#api-reference)

## Overview

### What's New?

Instead of manually replacing placeholders in a template file, you now:

1. **Define a configuration object** with your casino's parameters
2. **Pass it to a factory function** that generates the complete automation
3. **Run it** - the framework handles everything else

### Benefits

- ✅ **Type-safe**: All parameters are validated by Python's type system
- ✅ **Composable**: Reuse and customize existing configs
- ✅ **Maintainable**: All configuration in one place
- ✅ **Testable**: Easy to create test configurations
- ✅ **DRY**: No code duplication across casino implementations

### Architecture

```
CasinoConfig → make_casino_automation() → main() function → Run automation
```

## Quick Start

### Minimal Example

```python
from casino import (
    CasinoConfig, LoginConfig, MTBClaimConfig,
    CurrencyDisplayConfig, Currency,
    make_casino_automation, get_arg_parser
)

# 1. Define configuration
config = CasinoConfig(
    name="MyCasino",
    url="https://mycasino.com",
    login_url="https://mycasino.com/login",

    login=LoginConfig(
        username_selector='input[name="email"]',
        password_selector='input[name="password"]',
        login_submit_selector='button[type="submit"]',
    ),

    currency_display=CurrencyDisplayConfig(
        currencies=[
            Currency(name="Sweeps Coins", code="SC", selectors=['.sc-balance']),
            Currency(name="Gold Coins", code="GC", selectors=['.gc-balance']),
        ],
    ),

    claim_config=MTBClaimConfig(
        modal_selector='button.wallet',
        tab_selector='button.daily-bonus',
        btn_selector='button.claim',
        close_btn_selector='button.close',
    ),
    claim_pattern="mtb",
)

# 2. Generate main function
main = make_casino_automation(config)

# 3. Run it
if __name__ == "__main__":
    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()
    main(**vars(args))
```

That's it! The entire casino automation is ready to use.

## Configuration Classes

### CasinoConfig

The main configuration class that contains all parameters.

```python
@dataclass
class CasinoConfig:
    # Required fields
    name: str                              # Casino display name
    url: str                               # Main casino URL
    login_url: str                         # Login page URL
    login: LoginConfig                     # Login configuration
    currency_display: CurrencyDisplayConfig # Currency display config
    claim_config: MTBClaimConfig | GenericClaimConfig  # Claiming config

    # Optional fields with defaults
    claim_pattern: Literal["mtb", "generic"] = "mtb"
    custom_balance_parser: Optional[Callable] = None
    additional_actions: List[Callable] = []
    page_wait_timeout: int = 5000
    fetch_timeout: int = 60000
    description: str = "Casino Automation Script"
    requires_2fa: bool = False
```

### LoginConfig

Configuration for login form elements and behavior.

```python
@dataclass
class LoginConfig:
    username_selector: str                     # Username input selector
    password_selector: str                     # Password input selector
    login_submit_selector: str                 # Login button selector
    totp_code_selector: Optional[str] = None   # 2FA code input
    totp_submit_selector: Optional[str] = None # 2FA submit button
    pre_login_callback: Optional[Callable] = None   # Before login
    post_login_callback: Optional[Callable] = None  # After login
```

### MTBClaimConfig

Configuration for Modal-Tab-Button claiming pattern.

```python
@dataclass
class MTBClaimConfig:
    modal_selector: str       # Button to open modal (e.g., wallet)
    tab_selector: str         # Tab inside modal (e.g., daily bonus)
    btn_selector: str         # Claim button
    close_btn_selector: str   # Modal close button
```

### GenericClaimConfig

Configuration for Generic Accept/Close modal claiming pattern.

```python
@dataclass
class GenericClaimConfig:
    main_enabled_selector: str  # Enabled action buttons
    modal_selector: str          # Modal container
    close_modal_selector: str    # Close button
```

### CurrencyDisplayConfig

Configuration for how currencies are displayed.

```python
@dataclass
class CurrencyDisplayConfig:
    currencies: List[Currency]                           # List of currencies
    currency_toggle_dropdown_selector: Optional[str]     # Toggle dropdown
    currency_toggle_switch_selector: Optional[str]       # Switch button
```

### Currency

Configuration for a single currency type.

```python
@dataclass
class Currency:
    name: str                                    # Display name
    code: str                                    # Currency code (SC, GC)
    selectors: List[str]                         # Selectors to find balance
    is_active_selector: Optional[str] = None     # Check if active
    activate_selector: Optional[str] = None      # Click to activate
```

## Step-by-Step Guide

### Step 1: Create Login Configuration

```python
login_config = LoginConfig(
    username_selector='input[name="email"]',
    password_selector='input[name="password"]',
    login_submit_selector='button[type="submit"]',

    # Add if 2FA is used
    totp_code_selector='input[name="code"]',
    totp_submit_selector='button[type="submit"]',

    # Add if you need to click something before login
    pre_login_callback=lambda page: page.click('div[id="tab-login"]'),
)
```

### Step 2: Configure Currency Display

**Pattern A: Dropdown Toggle**

```python
currency_display_config = CurrencyDisplayConfig(
    currencies=[
        Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=['[data-testid="sweeps-balance"]'],
        ),
        Currency(
            name="Gold Coins",
            code="GC",
            selectors=['[data-testid="gold-balance"]'],
        ),
    ],
    # Click this to open currency dropdown
    currency_toggle_dropdown_selector='button[data-testid="coin-toggle"]',
    currency_toggle_switch_selector=None,
)
```

**Pattern B: Direct Display**

```python
currency_display_config = CurrencyDisplayConfig(
    currencies=[
        Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=['.sweeps_color .amount'],
        ),
        Currency(
            name="Gold Coins",
            code="GC",
            selectors=['.gold_color .amount'],
        ),
    ],
    # Both currencies visible at once
    currency_toggle_dropdown_selector=None,
    currency_toggle_switch_selector=None,
)
```

### Step 3: Choose Claiming Pattern

**Pattern A: Modal-Tab-Button (MTB)**

Use when claiming requires: Open modal → Click tab → Click claim

```python
claim_config = MTBClaimConfig(
    modal_selector='button[data-testid="wallet"]',
    tab_selector='button[data-testid="daily-bonus"]',
    btn_selector='button.claim-button',
    close_btn_selector='button[data-testid="modal-close"]',
)
claim_pattern = "mtb"
```

**Pattern B: Generic Accept/Close**

Use when the site shows automatic popups with claim buttons.

```python
claim_config = GenericClaimConfig(
    main_enabled_selector="section.modal button:enabled",
    modal_selector="section.modal",
    close_modal_selector="section.modal .close",
)
claim_pattern = "generic"
```

### Step 4: Create Complete Configuration

```python
config = CasinoConfig(
    name="MyCasino",
    url="https://mycasino.com",
    login_url="https://mycasino.com/login",
    description="MyCasino Automation",

    # Add the configs from steps 1-3
    login=login_config,
    currency_display=currency_display_config,
    claim_config=claim_config,
    claim_pattern=claim_pattern,

    # Optional customizations
    requires_2fa=True,  # Set to True if 2FA is mandatory
    page_wait_timeout=5000,
    fetch_timeout=60000,
)
```

### Step 5: Generate and Run

```python
# Generate the main function
main = make_casino_automation(config)

# Use with CLI
if __name__ == "__main__":
    parser = get_arg_parser(description=config.description)
    args = parser.parse_args()
    main(**vars(args))
```

## Real-World Examples

### Example 1: Stake.us (MTB Pattern)

```python
stake_config = CasinoConfig(
    name="Stake.us",
    url="https://stake.us",
    login_url="https://stake.us/?tab=login&modal=auth",

    login=LoginConfig(
        username_selector='input[name="emailOrName"]',
        password_selector='input[name="password"]',
        login_submit_selector='button[type="submit"]',
        totp_code_selector='input[name="code"]',
        totp_submit_selector='button[type="submit"]',
    ),

    currency_display=CurrencyDisplayConfig(
        currencies=[
            Currency(name="Sweeps Coins", code="SC",
                    selectors=['[data-testid="coin-toggle-currency-sweeps"]']),
            Currency(name="Gold Coins", code="GC",
                    selectors=['[data-testid="coin-toggle-currency-gold"]']),
        ],
        currency_toggle_dropdown_selector='button[data-testid="coin-toggle"]',
    ),

    claim_config=MTBClaimConfig(
        modal_selector='button[data-testid="wallet"]',
        tab_selector='button[data-testid="dailyBonus"]',
        btn_selector="button.justify-center:nth-child(4)",
        close_btn_selector='button[data-testid="modal-close"]',
    ),
    claim_pattern="mtb",
)

main = make_casino_automation(stake_config)
```

### Example 2: LuckyBird.io (Generic Pattern)

```python
def luckybird_balance_parser(page: Page) -> Dict[str, Optional[float]]:
    """Custom parser for LuckyBird's currency toggle."""
    balances = {"gold_coins": None, "sweeps_coins": None}
    # ... custom parsing logic ...
    return balances

luckybird_config = CasinoConfig(
    name="LuckyBird.io",
    url="https://luckybird.io/",
    login_url="https://luckybird.io/",

    login=LoginConfig(
        username_selector="form input:nth-child(1)",
        password_selector="form input:nth-child(2)",
        login_submit_selector="button.tw-mt-10",
        totp_code_selector=".loginTwoFactor_input > input",
        totp_submit_selector=".loginTwoFactor_button",
        # Click login tab before filling form
        pre_login_callback=lambda page: page.click('div[id="tab-login"]'),
    ),

    currency_display=CurrencyDisplayConfig(
        currencies=[
            Currency(name="Sweeps Coins", code="SC",
                    selectors=[".sweeps_color .amount"]),
            Currency(name="Gold Coins", code="GC",
                    selectors=[".gold_color .amount"]),
        ],
    ),

    claim_config=GenericClaimConfig(
        main_enabled_selector="section.dailyBonus_page button:enabled",
        modal_selector="section.dailyBonus_page",
        close_modal_selector="section.dailyBonus_page .commonAlert_close",
    ),
    claim_pattern="generic",

    # Use custom balance parser
    custom_balance_parser=luckybird_balance_parser,
    requires_2fa=True,
)

main = make_casino_automation(luckybird_config)
```

## Advanced Features

### Custom Balance Parser

If the default parser doesn't work, create a custom one:

```python
def my_custom_parser(page: Page) -> Dict[str, Optional[float]]:
    """Custom balance parser for unique casino layouts."""
    balances = {"gold_coins": None, "sweeps_coins": None}

    # Your custom parsing logic
    gc_text = page.locator('.my-gc-class').text_content()
    balances["gold_coins"] = float(gc_text.replace('GC', '').replace(',', ''))

    return balances

config = CasinoConfig(
    # ... other config ...
    custom_balance_parser=my_custom_parser,
)
```

### Additional Actions

Run custom actions after the standard flow:

```python
def check_vip_rewards(page: Page) -> None:
    """Navigate to VIP page and check rewards."""
    page.goto("https://mycasino.com/vip")
    page.wait_for_selector('.vip-rewards')
    # ... custom logic ...

def play_specific_game(page: Page) -> None:
    """Navigate to a specific game."""
    page.goto("https://mycasino.com/games/slots")
    # ... custom logic ...

config = CasinoConfig(
    # ... other config ...
    additional_actions=[
        check_vip_rewards,
        play_specific_game,
    ],
)
```

### Pre/Post Login Callbacks

Handle special cases before or after login:

```python
from casino import make_handle_google_one_tap_popup, close_selectors

def close_welcome_banner(page: Page) -> None:
    """Close welcome banner after login."""
    page.click('.welcome-banner .close', timeout=5000)

login_config = LoginConfig(
    # ... selectors ...
    pre_login_callback=make_handle_google_one_tap_popup(close_selectors),
    post_login_callback=close_welcome_banner,
)
```

## Migration Guide

### From Old Template to New System

**Before (Old Template):**

```python
url = "https://mycasino.com"
username_selector = 'input[name="email"]'
password_selector = 'input[name="password"]'
# ... many more variables ...

def main():
    # ... lots of boilerplate code ...
```

**After (New System):**

```python
config = CasinoConfig(
    name="MyCasino",
    url="https://mycasino.com",
    login_url="https://mycasino.com",
    login=LoginConfig(
        username_selector='input[name="email"]',
        password_selector='input[name="password"]',
        login_submit_selector='button[type="submit"]',
    ),
    # ... other config ...
)

main = make_casino_automation(config)
```

### Converting Existing Implementations

1. **Identify your pattern**: MTB or Generic claiming?
2. **Group selectors** into config objects
3. **Create the config** using the appropriate classes
4. **Remove boilerplate**: Factory handles everything

See `casino_configs_examples.py` for complete conversion examples.

## API Reference

### make_casino_automation(config: CasinoConfig) → Callable

Factory function that generates a complete casino automation.

**Parameters:**
- `config`: CasinoConfig object with all casino-specific parameters

**Returns:**
- A `main()` function that accepts: `headless`, `google_oauth`, `skip_claim`, `proxy`, `user_data_dir`

**Example:**
```python
config = CasinoConfig(...)
main = make_casino_automation(config)
main(headless=True, skip_claim=False)
```

### Configuration Validation

The system validates your configuration at runtime:

- Ensures `claim_pattern` matches `claim_config` type
- Validates required fields are provided
- Type checks all parameters

## Best Practices

1. **Use descriptive names** for custom functions
   ```python
   def parse_mycasino_balance(page: Page):  # Good
   def parser(page: Page):                   # Bad
   ```

2. **Group related selectors** in Currency objects
   ```python
   Currency(
       name="Sweeps Coins",
       code="SC",
       selectors=[
           '.sc-balance',           # Primary
           '[data-currency="SC"]',  # Fallback
           '.balance-sc',           # Another fallback
       ],
   )
   ```

3. **Reuse and customize** existing configs
   ```python
   # Start with a similar casino's config
   config = create_stake_us_config()
   # Customize for your needs
   config.name = "Stake Clone"
   config.url = "https://stakeclone.com"
   config.login.username_selector = 'input[name="user"]'
   ```

4. **Keep custom parsers simple** and well-documented
   ```python
   def parse_balance(page: Page) -> Dict[str, Optional[float]]:
       """Parse balances from unique DOM structure.

       This casino shows SC in a modal and GC in the header.
       """
       # Implementation...
   ```

5. **Test incrementally** as you build the config
   ```python
   # Test login first
   config = CasinoConfig(..., claim_pattern="mtb", claim_config=...)
   main = make_casino_automation(config)
   main(skip_claim=True)  # Test without claiming
   ```

## Troubleshooting

### Configuration Errors

**Error: `claim_pattern is 'mtb' but claim_config is not MTBClaimConfig`**

Solution: Ensure pattern matches config type:
```python
claim_config=MTBClaimConfig(...),
claim_pattern="mtb",  # Must match
```

**Error: `{SITE_PREFIX}_USERNAME and {SITE_PREFIX}_PASSWORD must be set`**

Solution: Set environment variables:
```bash
export MYCASINO_USERNAME="user"
export MYCASINO_PASSWORD="pass"
```

### Runtime Errors

**Balance shows as 0.0**

Solution: Use a custom balance parser or adjust selectors:
```python
custom_balance_parser=my_custom_parser,
```

**Claim doesn't work**

Solution: Try the other claiming pattern:
```python
# Switch from MTB to Generic or vice versa
claim_pattern="generic",
claim_config=GenericClaimConfig(...),
```

## Examples Repository

See these files for complete examples:

- `casino_template_parameterized.py` - Template with placeholders
- `casino_configs_examples.py` - Real-world examples (Stake.us, LuckyBird.io)
- `stake_us.py` - Original implementation (for comparison)
- `luckybird.py` - Original implementation (for comparison)

## Getting Help

1. Check the examples in `casino_configs_examples.py`
2. Review the old implementations for reference
3. Use `--skip-claim` to test login and balance reading separately
4. Run without `--headless` to see what's happening

## Contributing

When adding new casino configs:

1. Create the config in a new file or add to `casino_configs_examples.py`
2. Test thoroughly
3. Document any special requirements or custom parsers
4. Include environment variable requirements
