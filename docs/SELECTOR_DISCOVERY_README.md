# Casino Selector Discovery Tool

A powerful automated tool for discovering CSS selectors needed to automate casino sites. This tool can operate in both **passive mode** (analyzing pages without logging in) and **active mode** (using credentials to test login and discover authenticated selectors).

## Features

- 🔍 **Automatic Selector Detection**: Uses heuristics and pattern matching to find login, currency, and claim selectors
- 🎯 **Confidence Scoring**: Ranks selectors by confidence level based on multiple factors
- 🔐 **Login Testing**: Can actually attempt login to verify selectors work correctly
- 💰 **Currency Display Detection**: Finds balance displays for both Gold Coins and Sweeps Coins
- 🎁 **Claim Pattern Detection**: Automatically detects whether site uses MTB or Generic claim patterns
- 📤 **Config Export**: Generates ready-to-use Python config files
- 🔒 **2FA Support**: Handles TOTP-based two-factor authentication

## Installation

No additional dependencies beyond the main project requirements:

```bash
# The tool uses the existing project dependencies
uv sync
```

For 2FA support, ensure `pyotp` is installed:

```bash
pip install pyotp
```

## Usage

### Basic Usage (Passive Mode)

Analyze a casino site without logging in:

```bash
python casino_selector_discovery.py https://casino-site.com
```

This will:
- Load the casino site
- Detect login form selectors
- Attempt to find currency and claim selectors (limited accuracy without authentication)
- Print results with confidence scores

### Active Mode (With Login)

For more accurate results, provide credentials to actually test the login flow:

```bash
# Using environment variables (recommended)
export CASINO_USERNAME="your_username"
export CASINO_PASSWORD="your_password"
python casino_selector_discovery.py https://casino-site.com --test-login

# Or provide credentials directly
python casino_selector_discovery.py https://casino-site.com \
  --username "your_username" \
  --password "your_password" \
  --test-login
```

### With 2FA Support

If the site requires two-factor authentication:

```bash
export CASINO_USERNAME="your_username"
export CASINO_PASSWORD="your_password"
export CASINO_2FA="your_totp_secret"

python casino_selector_discovery.py https://casino-site.com --test-login
```

Or with explicit TOTP secret:

```bash
python casino_selector_discovery.py https://casino-site.com \
  --username "your_username" \
  --password "your_password" \
  --totp-secret "YOUR_TOTP_SECRET" \
  --test-login
```

### Export Results

Generate a ready-to-use Python configuration file:

```bash
python casino_selector_discovery.py https://casino-site.com \
  --test-login \
  --export config_new_casino.py
```

This creates a complete Python file with:
- All discovered selectors
- Proper `CasinoConfig` structure
- Ready to integrate into your automation

Export to JSON for further processing:

```bash
python casino_selector_discovery.py https://casino-site.com \
  --test-login \
  --json results.json
```

### Browser Options

```bash
# Show browser window (useful for debugging)
python casino_selector_discovery.py https://casino-site.com --no-headless

# Run in headless mode (default)
python casino_selector_discovery.py https://casino-site.com --headless

# Increase timeout for slow sites
python casino_selector_discovery.py https://casino-site.com --timeout 60000
```

## How It Works

### 1. Login Selector Detection

The tool searches for login form elements using multiple heuristics:

**Username/Email Detection:**
- Looks for `input` elements with `type="text"` or `type="email"`
- Matches against keywords: `username`, `email`, `user`, `login`, `account`
- Checks `name`, `id`, `placeholder`, and `class` attributes
- Generates confidence score based on keyword matches

**Password Detection:**
- Finds `input` elements with `type="password"`
- Excludes 2FA/TOTP fields
- High confidence score (0.95) as password fields are very reliable

**Submit Button Detection:**
- Searches for `button[type="submit"]` elements
- Matches against login keywords: `login`, `signin`, `sign-in`, `log-in`, `auth`
- Falls back to generic submit buttons if no specific match found

**2FA/TOTP Detection:**
- Identifies inputs with keywords: `totp`, `2fa`, `twofa`, `two-factor`, `code`, `token`
- Finds associated submit buttons

### 2. Currency Display Detection

Searches for balance displays for Gold Coins and Sweeps Coins:

**Pattern Matching:**
- Looks for elements containing numbers (regex: `\$?\d{1,3}(,\d{3})*(\.\d{2,})?`)
- Matches currency keywords:
  - Gold Coins: `gold`, `gc`, `gold coin`, `gold-coin`
  - Sweeps Coins: `sweeps`, `sweep`, `sc`, `sweep coin`, `sweepstakes`
- Checks text content, class names, IDs, and data-testid attributes

**Toggle Detection:**
- Finds buttons/switches for currency switching
- Looks for keywords: `currency`, `coin`, `balance`, `toggle`, `switch`

### 3. Bonus Claim Detection

Identifies bonus claiming UI patterns:

**Claim Button Detection:**
- Searches for buttons with keywords: `claim`, `bonus`, `daily`, `reward`, `collect`, `redeem`, `free`

**Modal Detection:**
- Finds modal opener buttons (wallet, account)
- Identifies modal containers (`[role="dialog"]`, `.modal`, etc.)
- Locates close buttons

**Pattern Recognition:**
- **MTB Pattern**: Detects if site uses Modal → Tab → Button flow
- **Generic Pattern**: Identifies simple popup/modal claim pattern

### 4. Selector Generation

For each detected element, generates the most reliable selector:

1. **data-testid** (highest priority): `[data-testid="wallet"]`
2. **ID attribute**: `#login-btn`
3. **Name attribute**: `input[name="username"]`
4. **Class-based**: `button.primary.login-btn`
5. **Tag name** (fallback): `button`

### 5. Confidence Scoring

Each selector candidate receives a confidence score (0.0 to 1.0):

- **Base confidence**: 0.5 + (0.2 × number of keyword matches)
- **Type bonuses**: +0.1 for perfect type matches (e.g., `email` input type)
- **Maximum**: Capped at 0.95-1.0 for very reliable selectors

## Output Format

The tool provides detailed output:

```
================================================================================
Casino Selector Discovery Results for: https://casino-site.com
================================================================================

📝 LOGIN SELECTORS:

  USERNAME:
    1. input[name="emailOrName"]
       Confidence: 0.90
       Reasons: Found email input with username keywords

  PASSWORD:
    1. input[name="password"]
       Confidence: 0.95
       Reasons: Found password input

  SUBMIT:
    1. button[type="submit"]
       Confidence: 0.85
       Reasons: Found submit button with login keywords

💰 CURRENCY SELECTORS:

  GOLD_COINS:
    1. [data-testid="coin-toggle-currency-gold"]
       Confidence: 0.85
       Reasons: Found element with gold coin keywords and number
       Text: '1,234.56 GC'

  SWEEPS_COINS:
    1. [data-testid="coin-toggle-currency-sweeps"]
       Confidence: 0.85
       Reasons: Found element with sweeps coin keywords and number
       Text: '56.78 SC'

🎁 CLAIM SELECTORS:

  MODAL_OPENER:
    1. button[data-testid="wallet"]
       Confidence: 0.80
       Reasons: Found potential modal opener button

  CLAIM_BTN:
    1. button[type="submit"]
       Confidence: 0.85
       Reasons: Found button with claim keywords
       Text: 'Claim Daily Bonus'

🎯 Detected Claim Pattern: MTB

================================================================================
```

## Environment Variables

The tool uses the standard casino credential format:

```bash
{SITE_PREFIX}_USERNAME    # Username/email
{SITE_PREFIX}_PASSWORD    # Password
{SITE_PREFIX}_2FA         # TOTP secret (if 2FA required)
```

The site prefix is automatically extracted from the URL:
- `https://stake.us` → `STAKE_USERNAME`, `STAKE_PASSWORD`
- `https://luckybird.io` → `LUCKYBIRD_USERNAME`, `LUCKYBIRD_PASSWORD`

## Examples

### Example 1: Discover Selectors for Stake.us

```bash
# Set credentials
export STAKE_USERNAME="user@example.com"
export STAKE_PASSWORD="password123"

# Run discovery
python casino_selector_discovery.py https://stake.us \
  --test-login \
  --export stake_us_discovered.py
```

### Example 2: Discover Selectors Without Login

```bash
# Passive discovery (no authentication)
python casino_selector_discovery.py https://newcasino.com \
  --json newcasino_discovery.json
```

### Example 3: Debug Discovery with Visible Browser

```bash
# Show browser window to see what's happening
python casino_selector_discovery.py https://casino.com \
  --no-headless \
  --test-login
```

### Example 4: Site with 2FA

```bash
export LUCKYBIRD_USERNAME="user@example.com"
export LUCKYBIRD_PASSWORD="password123"
export LUCKYBIRD_2FA="JBSWY3DPEHPK3PXP"  # Your TOTP secret

python casino_selector_discovery.py https://luckybird.io \
  --test-login \
  --export luckybird_discovered.py
```

## Integration with Existing Configs

After generating a config file, you can:

1. **Review and refine** the discovered selectors
2. **Add custom callbacks** for pre/post-login actions
3. **Implement custom balance parsers** if needed
4. **Test the configuration**:

```bash
# Test the generated config
python stake_us_discovered.py
```

## Limitations

### Passive Mode (No Login)
- Cannot discover selectors behind authentication
- Balance and claim selectors may not be accurate
- Cannot verify selectors actually work

### Active Mode (With Login)
- Requires valid credentials
- May trigger rate limiting or security measures
- Site-specific quirks may not be detected automatically

### General Limitations
- Dynamic/JavaScript-rendered content may not be fully detected
- Complex multi-step claim flows may need manual refinement
- Custom site behaviors require manual callback implementation
- Shadow DOM elements are not currently supported

## Tips for Best Results

1. **Use Active Mode**: Always use `--test-login` with credentials for best accuracy
2. **Review Confidence Scores**: Selectors with confidence < 0.7 should be manually verified
3. **Test Generated Configs**: Always test the exported config before using in production
4. **Refine Manually**: Use discovered selectors as a starting point, then refine as needed
5. **Check for Alternatives**: Multiple selector candidates are provided - choose the most stable one
6. **Handle Edge Cases**: Some sites require pre-login callbacks or custom balance parsers

## Troubleshooting

### "Could not find username or password selectors"
- The site may have a non-standard login form
- Try running with `--no-headless` to see the page structure
- Check if there's a separate login page or modal

### "Login may have failed - login form still visible"
- Credentials may be incorrect
- Site may have CAPTCHA or other anti-bot measures
- 2FA may be required but not configured

### "Discovery timeout"
- Increase timeout: `--timeout 60000`
- Site may be slow or have heavy JavaScript
- Check network connectivity

### Low confidence scores
- Site uses non-standard selectors
- Review the discovered selectors manually
- Consider adding custom heuristics for specific site patterns

## Advanced Usage

### Custom Heuristics

You can extend the discovery tool by modifying the keyword lists in `CasinoSelectorDiscovery`:

```python
# Add custom keywords for your specific casino sites
CURRENCY_KEYWORDS = {
    'gold_coins': ['gold', 'gc', 'coins', 'your-custom-keyword'],
    'sweeps_coins': ['sweeps', 'sc', 'your-custom-keyword'],
}
```

### Programmatic Usage

Use the discovery tool as a library:

```python
from casino_selector_discovery import CasinoSelectorDiscovery

with CasinoSelectorDiscovery("https://casino.com", headless=True) as discovery:
    result = discovery.discover(
        username="user",
        password="pass",
        test_login=True
    )

    # Access discovery results
    print(result.login_selectors)
    print(result.currency_selectors)
    print(result.detected_pattern)

    # Convert to CasinoConfig
    config = result.to_casino_config("My Casino")
```

## Contributing

To improve the selector discovery algorithm:

1. Add new keyword patterns for different selector types
2. Implement additional heuristics for edge cases
3. Improve confidence scoring algorithm
4. Add support for new claim patterns
5. Enhance element detection logic

## License

Part of the Gamba Pick casino automation project.
