# Casino Template Implementation Guide

This guide will help you create a new casino automation script using the provided template.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Step-by-Step Implementation](#step-by-step-implementation)
4. [Understanding the Patterns](#understanding-the-patterns)
5. [Testing Your Implementation](#testing-your-implementation)
6. [Common Issues and Solutions](#common-issues-and-solutions)
7. [Examples](#examples)

## Prerequisites

Before you begin, ensure you have:

- Python 3.8+
- Required packages installed (see `requirements.txt` or `pyproject.toml`)
- Browser developer tools knowledge (Chrome DevTools, Firefox Inspector, etc.)
- Access to the casino website you want to automate
- Valid credentials for the casino

## Quick Start

1. **Copy the template:**
   ```bash
   cp casino_template.py mynewcasino.py
   ```

2. **Set environment variables:**
   ```bash
   export MYNEWCASINO_USERNAME="your_username"
   export MYNEWCASINO_PASSWORD="your_password"
   export MYNEWCASINO_2FA="your_totp_secret"  # Optional, if 2FA enabled
   ```

3. **Find selectors using browser DevTools:**
   - Open the casino website
   - Right-click on elements and "Inspect"
   - Copy CSS selectors for login fields, buttons, etc.

4. **Update the template with your selectors**

5. **Test your implementation:**
   ```bash
   python mynewcasino.py  # Run with browser visible
   python mynewcasino.py --headless  # Run in background
   ```

## Step-by-Step Implementation

### Step 1: Configure URLs

```python
url = "https://yourcasino.com"
login_url = "https://yourcasino.com/?modal=login"  # Or same as url
```

**How to find:**
- `url`: The main website URL
- `login_url`: The URL that shows the login form (may be the same as `url`)

### Step 2: Configure Login Selectors

These are the CSS selectors for the login form elements.

```python
username_selector = 'input[name="email"]'
password_selector = 'input[name="password"]'
login_submit_selector = 'button[type="submit"]'
totp_code_selector = 'input[name="code"]'  # Optional
```

**How to find selectors:**

1. Open the casino website in your browser
2. Right-click on the username field → "Inspect"
3. In the DevTools Elements panel, right-click the highlighted HTML element
4. Select "Copy" → "Copy selector"
5. Paste the selector into your script

**Common selector patterns:**
- By ID: `#username` or `input#username`
- By name attribute: `input[name="username"]`
- By data attribute: `input[data-testid="username-input"]`
- By class: `.username-field` or `input.username-field`
- By nested structure: `form.login > div > input:nth-child(1)`

**Pro tip:** Use multiple selectors as fallbacks:
```python
username_selector = 'input[name="username"], input#username, .username-input'
```

### Step 3: Configure Currency Display Selectors

There are two common patterns for currency display:

#### Pattern A: Dropdown Toggle (like Stake.us)

The site has a button that opens a dropdown showing both currencies.

```python
currency_toggle_dropdown_selector = 'button[data-testid="coin-toggle"]'
currency_toggle_switch_selector = None  # or a selector to switch between currencies

sweeps_coins_selectors = ['[data-testid="sweeps-balance"]']
gold_coins_selectors = ['[data-testid="gold-balance"]']
```

#### Pattern B: Direct Display (like LuckyBird.io)

Both currencies are visible on the page at the same time.

```python
currency_toggle_dropdown_selector = None
currency_toggle_switch_selector = None

sweeps_coins_selectors = ['.sweeps_color .amount']
gold_coins_selectors = ['.gold_color .amount']
```

**How to find currency selectors:**

1. Log into the casino website
2. Look for your balance display (usually top-right corner)
3. Inspect the element showing the SC (Sweeps Coins) amount
4. Copy the selector
5. Repeat for GC (Gold Coins)

**Common balance formats:**
- `SC 1,234.56`
- `1234.56 SC`
- Separate elements for currency code and amount

### Step 4: Configure Bonus Claiming Selectors

There are two main patterns for claiming daily bonuses:

#### Pattern 1: Modal-Tab-Button (MTB)

**Use when:** Claiming requires opening a modal, clicking a tab, then clicking claim.

**Example flow:** Click "Wallet" → Click "Daily Bonus" tab → Click "Claim" button

```python
wallet_btn_selector = 'button[data-testid="wallet"]'
daily_bonus_tab_selector = 'button[data-testid="dailyBonus"]'
claim_btn_selector = 'button.claim-button'
close_btn_selector = 'button[data-testid="modal-close"]'

# Use the MTB pattern
claim_bonus_action = make_modal_tab_button(
    modal_selector=wallet_btn_selector,
    tab_selector=daily_bonus_tab_selector,
    btn_selector=claim_btn_selector,
    close_btn_selector=close_btn_selector,
)
```

#### Pattern 2: Generic Accept/Close Modals

**Use when:** The site automatically shows popups/modals with claim buttons.

**Example flow:** Popup appears → Click "Claim" or "Accept" → Popup closes

```python
main_enabled_selector = "section.modal button.primary:enabled"
modal_selector = "section.modal"
close_modal_selector = "section.modal .close-button"

# Use the Generic pattern
claim_bonus_action = make_generic_accept_or_close_modals(
    main_enabled_selector=main_enabled_selector,
    modal_selector=modal_selector,
    close_modal_selector=close_modal_selector,
)
```

### Step 5: Handle Special Cases

#### Pre-login Callback

Some sites require additional actions before logging in:

```python
# Example: Click a "Login" tab before filling the form
login_action_factory = make_login_action_factory(
    username_selector=username_selector,
    password_selector=password_selector,
    login_submit_selector=login_submit_selector,
    pre_login_form_callback=lambda page: page.click('div[id="tab-login"]'),
)
```

#### Custom Balance Parser

If the default parser doesn't work, create a custom one:

```python
def parse_coin_balances_custom(page: Page) -> Dict[str, Optional[float]]:
    balances = {"gold_coins": None, "sweeps_coins": None}

    # Custom logic to extract balances
    gc_text = page.locator('.gold-balance').text_content()
    balances["gold_coins"] = float(gc_text.replace('GC', '').replace(',', ''))

    return balances
```

Then use it in your `casino_action` function:

```python
balances = parse_coin_balances_custom(page)
log.info("Balances: %s", balances)
```

## Understanding the Patterns

### The Casino Action Flow

Every casino automation follows this general flow:

```
1. Open browser session (StealthySession)
2. Navigate to login URL
3. Execute casino_action:
   a. Handle popups (Google One Tap, etc.)
   b. Fill login form
   c. Submit login form
   d. Handle 2FA (if needed)
   e. Wait for page load
   f. Read account balances
   g. Claim daily bonus
   h. Perform additional actions (optional)
4. Close browser session
```

### Factory Pattern

The template uses factory functions to create reusable actions:

- `make_login_action_factory()`: Creates a login function with your selectors
- `make_modal_tab_button()`: Creates a claim function for MTB pattern
- `make_generic_accept_or_close_modals()`: Creates a claim function for popup pattern
- `make_get_casino_account_state()`: Creates a balance parser function

This pattern allows you to configure once and reuse across different sites.

### Currency Display Configuration

The `CurrencyDisplayConfig` class standardizes how we read different currency types:

```python
currency_display_config = CurrencyDisplayConfig(
    currencies=[
        Currency(name="Sweeps Coins", code="SC", selectors=['...']),
        Currency(name="Gold Coins", code="GC", selectors=['...']),
    ],
    currency_toggle_dropdown_selector='...',  # How to open currency view
    currency_toggle_switch_selector='...',    # How to switch between currencies
)
```

## Testing Your Implementation

### 1. Visual Testing (Recommended for first run)

```bash
python mynewcasino.py
```

Watch the browser to see if:
- Login form fills correctly
- Login submits successfully
- Balances are read correctly
- Bonus claim works

### 2. Headless Testing

```bash
python mynewcasino.py --headless
```

### 3. Skip Claim Testing

Test without claiming the bonus:

```bash
python mynewcasino.py --skip-claim
```

### 4. Proxy Testing

```bash
python mynewcasino.py --proxy http://user:pass@proxy:port
```

### 5. Debug Mode

Check the logs for detailed information:

```bash
python mynewcasino.py 2>&1 | tee debug.log
```

## Common Issues and Solutions

### Issue: "Element not found"

**Solution:** Your selector is incorrect or the element takes time to load.

```python
# Add wait before interacting
page.wait_for_selector(username_selector, timeout=10000)
page.fill(username_selector, username)
```

### Issue: "Login fails"

**Possible causes:**
1. Incorrect selectors
2. Google One Tap popup blocking the form
3. Missing pre-login callback
4. Captcha protection

**Solutions:**
```python
# Add pre-login callback to handle popups
pre_login_form_callback=make_handle_google_one_tap_popup(close_selectors)

# Add delays
page.wait_for_timeout(2000)
```

### Issue: "Balance shows as 0.0"

**Possible causes:**
1. Wrong currency selectors
2. Balance in different format (e.g., "1,234.56" vs "1234.56")
3. Balance in an iframe

**Solutions:**
1. Verify selectors in DevTools
2. Create custom balance parser
3. Check if balance is in an iframe:
```python
iframe = page.frame_locator('iframe#balance-frame')
balance = iframe.locator('.balance').text_content()
```

### Issue: "Bonus claim doesn't work"

**Possible causes:**
1. Wrong pattern (using MTB when should use Generic)
2. Incorrect selectors
3. Bonus already claimed

**Solutions:**
1. Try the other pattern
2. Run with `--skip-claim` to verify everything else works
3. Check if bonus is available manually

### Issue: "2FA not working"

**Possible causes:**
1. Wrong TOTP secret
2. Incorrect selector for 2FA input
3. Time sync issues

**Solutions:**
```python
# Verify TOTP secret generates correct codes
import pyotp
totp = pyotp.TOTP("your_secret")
print(totp.now())  # Should match casino's code

# Add longer timeout for 2FA
page.wait_for_selector(totp_code_selector, timeout=15000)
```

## Examples

### Example 1: Simple Casino (like Stake.us)

```python
url = "https://stake.us"
login_url = "https://stake.us/?modal=auth&tab=login"

username_selector = 'input[name="emailOrName"]'
password_selector = 'input[name="password"]'
login_submit_selector = 'button[type="submit"]'

currency_toggle_dropdown_selector = 'button[data-testid="coin-toggle"]'
sweeps_coins_selectors = ['[data-testid="coin-toggle-currency-sweeps"]']
gold_coins_selectors = ['[data-testid="coin-toggle-currency-gold"]']

# MTB Pattern
wallet_btn_selector = 'button[data-testid="wallet"]'
daily_bonus_tab_selector = 'button[data-testid="dailyBonus"]'
claim_btn_selector = "button.justify-center:nth-child(4)"
close_btn_selector = 'button[data-testid="modal-close"]'

claim_bonus_action = make_modal_tab_button(
    modal_selector=wallet_btn_selector,
    tab_selector=daily_bonus_tab_selector,
    btn_selector=claim_btn_selector,
    close_btn_selector=close_btn_selector,
)
```

### Example 2: Casino with Automatic Popups (like LuckyBird.io)

```python
url = "https://luckybird.io"
login_url = "https://luckybird.io"

username_selector = "form input:nth-child(1)"
password_selector = "form input:nth-child(2)"
login_submit_selector = "button.submit"

# Pre-login: Click login tab
pre_login_callback = lambda page: page.click('div[id="tab-login"]')

# Direct balance display
currency_toggle_dropdown_selector = None
sweeps_coins_selectors = ['.sweeps_color .amount']
gold_coins_selectors = ['.gold_color .amount']

# Generic Accept/Close pattern
main_enabled_selector = "section.dailyBonus_page button:enabled"
modal_selector = "section.dailyBonus_page"
close_modal_selector = "section.dailyBonus_page .close"

claim_bonus_action = make_generic_accept_or_close_modals(
    main_enabled_selector=main_enabled_selector,
    modal_selector=modal_selector,
    close_modal_selector=close_modal_selector,
)
```

### Example 3: Casino with Custom Balance Parser

```python
def parse_coin_balances_custom(page: Page) -> Dict[str, Optional[float]]:
    """Custom parser for a casino with unique balance display."""
    balances = {"gold_coins": None, "sweeps_coins": None}
    number_pattern = re.compile(r"[\d,]+\.?\d*")

    try:
        # Gold coins in a specific div
        gc_div = page.locator('div.wallet-gc')
        if gc_div.count() > 0:
            text = gc_div.text_content()
            match = number_pattern.search(text)
            if match:
                balances["gold_coins"] = float(match.group().replace(',', ''))

        # Sweeps coins require clicking a toggle
        page.click('.currency-toggle')
        page.wait_for_timeout(500)

        sc_div = page.locator('div.wallet-sc')
        if sc_div.count() > 0:
            text = sc_div.text_content()
            match = number_pattern.search(text)
            if match:
                balances["sweeps_coins"] = float(match.group().replace(',', ''))

    except Exception as e:
        log.error("Error parsing balances: %s", e)

    return balances
```

## Best Practices

1. **Always provide multiple fallback selectors**
   ```python
   username_selector = 'input[name="user"], input#username, .user-input'
   ```

2. **Add appropriate waits**
   ```python
   wait_for_load_all_safe(page)
   page.wait_for_timeout(1000)  # Additional wait if needed
   ```

3. **Use descriptive variable names**
   ```python
   # Good
   daily_bonus_claim_button = 'button.claim-daily'

   # Bad
   btn1 = 'button.claim-daily'
   ```

4. **Log important steps**
   ```python
   log.info("Starting login process")
   login_action(page)
   log.info("Login completed")
   ```

5. **Handle errors gracefully**
   ```python
   try:
       claim_bonus_action(page)
   except Exception as e:
       log.error("Bonus claim failed: %s", e)
       # Continue with rest of script
   ```

6. **Test incrementally**
   - First: Test login only
   - Second: Test balance reading
   - Third: Test bonus claiming
   - Finally: Run complete script

## Environment Variables

Set these before running your script:

```bash
# For a casino at https://mycasino.com
export MYCASINO_USERNAME="your_username"
export MYCASINO_PASSWORD="your_password"
export MYCASINO_2FA="JBSWY3DPEHPK3PXP"  # Optional TOTP secret

# The prefix is derived from the domain name
# stake.us → STAKE
# luckybird.io → LUCKYBIRD
# my-casino.com → MY-CASINO (with dash)
```

## Command-Line Options

All scripts support these options:

```bash
# Run with visible browser
python mycasino.py

# Run in headless mode
python mycasino.py --headless

# Use a proxy
python mycasino.py --proxy http://user:pass@proxy:8080

# Skip claiming the bonus
python mycasino.py --skip-claim

# Use a specific browser profile
python mycasino.py --user-data-dir /path/to/profile

# Combine options
python mycasino.py --headless --skip-claim --proxy http://proxy:8080
```

## Getting Help

If you encounter issues:

1. Check the logs for error messages
2. Run without `--headless` to see what's happening
3. Verify selectors are still valid (websites change)
4. Check if the casino added captcha or other protections
5. Try the `--skip-claim` flag to isolate issues

## Contributing

When creating a new casino implementation:

1. Test thoroughly
2. Document any special requirements
3. Add comments explaining non-obvious selectors
4. Include example environment variable setup
5. Note any known limitations

## Security Notes

- Never commit credentials or TOTP secrets to version control
- Use environment variables for sensitive data
- Consider using a password manager or secrets management system
- Rotate credentials regularly
- Use 2FA when available

## License

See the main project license.
