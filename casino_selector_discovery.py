"""
Casino Selector Discovery Module

This module provides automated detection of CSS selectors for casino sites
based on common patterns and heuristics. It can operate in two modes:

1. Passive mode: Analyzes the page without logging in (limited accuracy)
2. Active mode: Uses credentials to login and test selectors (higher accuracy)

Usage:
    python casino_selector_discovery.py https://casino-site.com
    python casino_selector_discovery.py https://casino-site.com --username user --password pass
    python casino_selector_discovery.py https://casino-site.com --headless --test-login
"""

import re
import sys
import argparse
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from urllib.parse import urlparse
import json

from playwright.sync_api import sync_playwright, Page, Browser, ElementHandle

# Import StealthySession for stealth browsing capabilities
try:
    from scrapling.fetchers import StealthySession
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

# Import only what we need from casino.py to avoid scrapling dependency
try:
    from casino import (
        CasinoConfig,
        LoginConfig,
        CurrencyDisplayConfig,
        Currency,
        MTBClaimConfig,
        GenericClaimConfig,
        get_credentials,
        url_to_env_prefix,
        wait_for_load_all_safe,
        gaussian_random_delay,
    )
except ImportError:
    # Fallback: define minimal versions if casino.py can't be imported
    # This allows the tool to work standalone if scrapling isn't available

    from typing import Callable
    import time
    import os
    import random

    class Currency:
        """Represents a currency with its associated selectors."""
        def __init__(
            self,
            name: str,
            code: str,
            selectors: List[str],
            is_active_selector: Optional[str] = None,
            activate_selector: Optional[str] = None,
        ):
            self.name = name
            self.code = code
            self.selectors = selectors
            self.is_active_selector = is_active_selector
            self.activate_selector = activate_selector

    class CurrencyDisplayConfig:
        """Configuration for currency selectors."""
        def __init__(
            self,
            currencies: List["Currency"],
            currency_toggle_dropdown_selector: Optional[str] = None,
            currency_toggle_switch_selector: Optional[str] = None,
        ):
            self.currencies = currencies
            self.currency_toggle_dropdown_selector = currency_toggle_dropdown_selector
            self.currency_toggle_switch_selector = currency_toggle_switch_selector

    @dataclass
    class LoginConfig:
        """Configuration for login process."""
        username_selector: str
        password_selector: str
        login_submit_selector: str
        totp_code_selector: Optional[str] = None
        totp_submit_selector: Optional[str] = None
        pre_login_callback: Optional[Callable[[Page], None]] = None
        post_login_callback: Optional[Callable[[Page], None]] = None

    @dataclass
    class MTBClaimConfig:
        """Modal-Tab-Button claim pattern configuration."""
        modal_selector: str
        tab_selector: str
        btn_selector: str
        close_btn_selector: str

    @dataclass
    class GenericClaimConfig:
        """Generic popup claim pattern configuration."""
        main_enabled_selector: str
        modal_selector: str
        close_modal_selector: str

    @dataclass
    class CasinoConfig:
        """Complete casino configuration."""
        name: str
        url: str
        login_url: str
        login: LoginConfig
        currency_display: CurrencyDisplayConfig
        claim_config: Optional[Any] = None
        claim_pattern: Optional[str] = None
        requires_2fa: bool = False
        page_wait_timeout: int = 5000
        fetch_timeout: int = 60000

    # def url_to_env_prefix(url: str) -> str:
    #     """Convert URL to environment variable prefix."""
    #     parsed = urlparse(url)
    #     domain = parsed.netloc.replace('www.', '').split('.')[0]
    #     return domain.upper()

    # def get_credentials(url: str, twofa: bool = False) -> Dict[str, Optional[str]]:
    #     """Get credentials from environment variables."""
    #     prefix = url_to_env_prefix(url)
    #     return {
    #         'username': os.environ.get(f'{prefix}_USERNAME'),
    #         'password': os.environ.get(f'{prefix}_PASSWORD'),
    #         'twofa': os.environ.get(f'{prefix}_2FA') if twofa else None,
    #     }

    def wait_for_load_all_safe(page: Page, timeout: int = 500) -> None:
        """Wait for page to finish loading."""
        try:
            page.wait_for_load_state('networkidle', timeout=timeout)
        except Exception:
            pass

    def gaussian_random_delay(mean: float = 50, stddev: float = 10) -> int:
        """Generate random delay with Gaussian distribution."""
        import random
        delay = max(10, int(random.gauss(mean, stddev)))
        return delay


@dataclass
class SelectorCandidate:
    """Represents a potential selector with confidence score."""
    selector: str
    confidence: float  # 0.0 to 1.0
    element_count: int
    reasons: List[str] = field(default_factory=list)
    element_text: Optional[str] = None
    element_attrs: Dict[str, str] = field(default_factory=dict)

    def __repr__(self):
        return f"SelectorCandidate(selector='{self.selector}', confidence={self.confidence:.2f}, reasons={self.reasons})"


@dataclass
class DiscoveryResult:
    """Complete discovery result for a casino site."""
    url: str
    login_selectors: Dict[str, List[SelectorCandidate]]
    currency_selectors: Dict[str, List[SelectorCandidate]]
    claim_selectors: Dict[str, List[SelectorCandidate]]
    detected_pattern: Optional[str] = None  # 'mtb' or 'generic'
    requires_2fa: bool = False
    errors: List[str] = field(default_factory=list)

    def to_casino_config(self, name: Optional[str] = None) -> CasinoConfig:
        """Convert discovery result to a CasinoConfig object."""
        if name is None:
            parsed = urlparse(self.url)
            name = parsed.netloc.replace('www.', '').split('.')[0].title()

        # Get best login selectors
        login_config = LoginConfig(
            username_selector=self._get_best_selector(self.login_selectors.get('username', [])),
            password_selector=self._get_best_selector(self.login_selectors.get('password', [])),
            login_submit_selector=self._get_best_selector(self.login_selectors.get('submit', [])),
            totp_code_selector=self._get_best_selector(self.login_selectors.get('totp_code', [])) if self.requires_2fa else None,
            totp_submit_selector=self._get_best_selector(self.login_selectors.get('totp_submit', [])) if self.requires_2fa else None,
        )

        # Get currency display selectors
        currencies = []
        for currency_type in ['gold_coins', 'sweeps_coins']:
            candidates = self.currency_selectors.get(currency_type, [])
            if candidates:
                selectors = [c.selector for c in sorted(candidates, key=lambda x: x.confidence, reverse=True)[:3]]
                currencies.append(Currency(
                    name=currency_type.replace('_', ' ').title(),
                    code='GC' if 'gold' in currency_type else 'SC',
                    selectors=selectors,
                ))

        currency_display_config = CurrencyDisplayConfig(
            currencies=currencies,
            currency_toggle_dropdown_selector=self._get_best_selector(
                self.currency_selectors.get('toggle_dropdown', [])
            ),
            currency_toggle_switch_selector=self._get_best_selector(
                self.currency_selectors.get('toggle_switch', [])
            ),
        )

        # Get claim config based on detected pattern
        claim_config = None
        if self.detected_pattern == 'mtb':
            claim_config = MTBClaimConfig(
                modal_selector=self._get_best_selector(self.claim_selectors.get('modal_opener', [])),
                tab_selector=self._get_best_selector(self.claim_selectors.get('tab', [])),
                btn_selector=self._get_best_selector(self.claim_selectors.get('claim_btn', [])),
                close_btn_selector=self._get_best_selector(self.claim_selectors.get('close_btn', [])),
            )
        elif self.detected_pattern == 'generic':
            claim_config = GenericClaimConfig(
                main_enabled_selector=self._get_best_selector(self.claim_selectors.get('enabled_btn', [])),
                modal_selector=self._get_best_selector(self.claim_selectors.get('modal', [])),
                close_modal_selector=self._get_best_selector(self.claim_selectors.get('close_btn', [])),
            )

        return CasinoConfig(
            name=name,
            url=self.url,
            login_url=self.url,  # May need manual adjustment
            login=login_config,
            currency_display=currency_display_config,
            claim_config=claim_config,
            claim_pattern=self.detected_pattern,
            requires_2fa=self.requires_2fa,
        )

    def _get_best_selector(self, candidates: List[SelectorCandidate]) -> Optional[str]:
        """Get the selector with highest confidence."""
        if not candidates:
            return None
        return max(candidates, key=lambda x: x.confidence).selector


class CasinoSelectorDiscovery:
    """Main class for discovering casino site selectors."""

    # Common keywords for different element types
    LOGIN_KEYWORDS = ['login', 'signin', 'sign-in', 'log-in', 'auth']
    LOGIN_PAGE_KEYWORDS = ['login', 'log in', 'signin', 'sign in', 'log-in', 'sign-in']
    LOGIN_HREF_KEYWORDS = ['login', 'signin', 'sign-in', 'log-in', 'auth', 'account/login']
    USERNAME_KEYWORDS = ['username', 'email', 'user', 'login', 'account']
    PASSWORD_KEYWORDS = ['password', 'pass', 'pwd']
    TOTP_KEYWORDS = ['totp', '2fa', 'twofa', 'two-factor', 'code', 'token', 'authenticator']

    CURRENCY_KEYWORDS = {
        'gold_coins': ['gold', 'gc', 'gold coin', 'gold-coin', 'goldcoin'],
        'sweeps_coins': ['sweeps', 'sweep', 'sc', 'sweep coin', 'sweeps coin', 'sweepstakes'],
    }

    CLAIM_KEYWORDS = ['claim', 'bonus', 'daily', 'reward', 'collect', 'redeem', 'free']
    MODAL_KEYWORDS = ['modal', 'dialog', 'popup', 'overlay', 'wallet', 'account']
    CLOSE_KEYWORDS = ['close', 'dismiss', 'cancel', 'exit']

    def __init__(
        self,
        url: str,
        headless: bool = True,
        timeout: int = 30000,
        # Stealth parameters
        proxy: Optional[str] = None,
        humanize: bool = True,
        solve_cloudflare: bool = False,
        block_webrtc: bool = False,
        geoip: bool = False,
        user_data_dir: Optional[str] = None,
        additional_args: Optional[Dict] = None,
        use_stealth: bool = True,
    ):
        self.url = url
        self.headless = headless
        self.timeout = timeout

        # Stealth configuration
        self.use_stealth = use_stealth and STEALTH_AVAILABLE
        self.proxy = proxy
        self.humanize = humanize
        self.solve_cloudflare = solve_cloudflare
        self.block_webrtc = block_webrtc
        self.geoip = geoip
        self.user_data_dir = user_data_dir
        self.additional_args = additional_args or {}

        # Runtime attributes
        self.page: Optional[Page] = None
        self.browser: Optional[Browser] = None
        self.playwright = None
        self._session = None  # StealthySession instance when using stealth mode

    def _build_additional_args(self) -> Dict:
        """Build additional args dict for StealthySession."""
        args = dict(self.additional_args)
        if self.user_data_dir:
            args["user_data_dir"] = self.user_data_dir
        return args

    def __enter__(self):
        if self.use_stealth:
            # Use StealthySession for stealth browsing capabilities
            self._session = StealthySession(
                headless=self.headless,
                proxy=self.proxy,
                humanize=self.humanize,
                solve_cloudflare=self.solve_cloudflare,
                block_webrtc=self.block_webrtc,
                geoip=self.geoip,
                timeout=self.timeout,
                additional_args=self._build_additional_args(),
            )
            self._session.__enter__()

            # Use _get_page() to get a page with stealth scripts applied
            # This injects the compiled stealth JS (webdriver_fully.js, window_chrome.js, etc.)
            page_info = self._session._get_page(self.timeout, None, False)
            self.page = page_info.page
            self.playwright = self._session.playwright
            self.browser = None  # Not directly accessible in StealthySession
        else:
            # Fallback: raw Playwright (no stealth)
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.headless)
            context = self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            self.page = context.new_page()
            self.page.set_default_timeout(self.timeout)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.use_stealth and self._session:
            # Close the page we created manually
            if self.page:
                try:
                    self.page.close()
                except Exception:
                    pass
                self.page = None
            # Let StealthySession clean up its resources
            self._session.__exit__(exc_type, exc_val, exc_tb)
            self._session = None
        else:
            # Fallback cleanup for raw Playwright
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()

    def discover(self, username: Optional[str] = None, password: Optional[str] = None,
                 totp_secret: Optional[str] = None, test_login: bool = False) -> DiscoveryResult:
        """
        Main discovery method.

        Args:
            username: Optional username for authenticated discovery
            password: Optional password for authenticated discovery
            totp_secret: Optional TOTP secret for 2FA
            test_login: Whether to actually attempt login

        Returns:
            DiscoveryResult with all discovered selectors
        """
        result = DiscoveryResult(
            url=self.url,
            login_selectors={},
            currency_selectors={},
            claim_selectors={},
        )

        try:
            # Load the initial page
            print(f"Loading {self.url}...")
            self.page.goto(self.url, wait_until='networkidle', timeout=self.timeout)
            wait_for_load_all_safe(self.page, timeout=1000)

            # Discover login selectors
            print("Discovering login selectors...")
            result.login_selectors = self._discover_login_selectors()

            # If no login form found, try to discover the login page URL
            has_login_form = (
                result.login_selectors.get('username') and
                result.login_selectors.get('password')
            )
            if not has_login_form:
                print("No login form found on current page, searching for login page link...")
                login_page_url = self._discover_login_page()
                if login_page_url:
                    print(f"Navigating to login page: {login_page_url}")
                    self.page.goto(login_page_url, wait_until='networkidle', timeout=self.timeout)
                    wait_for_load_all_safe(self.page, timeout=1000)

                    # Try discovering login selectors again on the login page
                    print("Discovering login selectors on login page...")
                    result.login_selectors = self._discover_login_selectors()

            # If credentials provided, attempt login and discover authenticated selectors
            if test_login and username and password:
                print("Attempting login to discover authenticated selectors...")
                login_success = self._attempt_login(username, password, totp_secret, result)

                if login_success:
                    print("Login successful! Discovering currency and claim selectors...")
                    result.currency_selectors = self._discover_currency_selectors()
                    result.claim_selectors = self._discover_claim_selectors()
                    result.detected_pattern = self._detect_claim_pattern(result.claim_selectors)
                else:
                    result.errors.append("Login failed - could not discover authenticated selectors")
            else:
                # Without credentials, only login selectors are meaningful
                print("Skipping currency/claim discovery (requires authentication)")

        except Exception as e:
            result.errors.append(f"Discovery error: {str(e)}")

        return result

    def _discover_login_selectors(self) -> Dict[str, List[SelectorCandidate]]:
        """Discover login form selectors."""
        selectors = {
            'username': [],
            'password': [],
            'submit': [],
            'totp_code': [],
            'totp_submit': [],
        }

        # Find all input elements
        inputs = self.page.locator('input').all()

        for input_el in inputs:
            try:
                input_type = input_el.get_attribute('type') or 'text'
                input_name = input_el.get_attribute('name') or ''
                input_id = input_el.get_attribute('id') or ''
                input_placeholder = input_el.get_attribute('placeholder') or ''
                input_class = input_el.get_attribute('class') or ''

                # Combine all text attributes for keyword matching
                combined_text = f"{input_name} {input_id} {input_placeholder} {input_class}".lower()

                # Check for username/email input
                if input_type in ['text', 'email'] and any(kw in combined_text for kw in self.USERNAME_KEYWORDS):
                    selector = self._generate_selector(input_el)
                    confidence = self._calculate_confidence(combined_text, self.USERNAME_KEYWORDS, input_type in ['email'])
                    selectors['username'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=[f"Found {input_type} input with username keywords"],
                        element_attrs={'name': input_name, 'type': input_type}
                    ))

                # Check for password input
                if input_type == 'password' and not any(kw in combined_text for kw in self.TOTP_KEYWORDS):
                    selector = self._generate_selector(input_el)
                    confidence = 0.95  # Password inputs are highly reliable
                    selectors['password'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found password input"],
                        element_attrs={'name': input_name, 'type': input_type}
                    ))

                # Check for TOTP/2FA code input
                if any(kw in combined_text for kw in self.TOTP_KEYWORDS):
                    selector = self._generate_selector(input_el)
                    confidence = self._calculate_confidence(combined_text, self.TOTP_KEYWORDS, input_type == 'text')
                    selectors['totp_code'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found 2FA/TOTP input"],
                        element_attrs={'name': input_name, 'type': input_type}
                    ))

            except Exception as e:
                continue

        # Find submit buttons
        buttons = self.page.locator('button[type="submit"]').all()
        for btn in buttons:
            try:
                btn_text = btn.inner_text().lower()
                btn_class = btn.get_attribute('class') or ''
                combined = f"{btn_text} {btn_class}".lower()

                if any(kw in combined for kw in self.LOGIN_KEYWORDS):
                    selector = self._generate_selector(btn)
                    confidence = self._calculate_confidence(combined, self.LOGIN_KEYWORDS)
                    selectors['submit'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found submit button with login keywords"],
                        element_text=btn_text
                    ))
            except Exception:
                continue

        # If no specific login submit found, add generic submit buttons
        if not selectors['submit']:
            generic_submits = self.page.locator('button[type="submit"]').all()
            for btn in generic_submits[:3]:  # Limit to first 3
                try:
                    selector = self._generate_selector(btn)
                    selectors['submit'].append(SelectorCandidate(
                        selector=selector,
                        confidence=0.5,
                        element_count=1,
                        reasons=["Generic submit button"],
                    ))
                except Exception:
                    continue

        return selectors

    def _discover_login_page(self) -> Optional[str]:
        """
        Discover the login page URL when no login form is found on the current page.

        Looks for links/buttons with login-related text or hrefs pointing to login pages.

        Returns:
            Login page URL if found, None otherwise
        """
        candidates: List[Tuple[str, float, str]] = []  # (url, confidence, reason)

        # Search for <a> tags with login text or href
        links = self.page.locator('a').all()
        for link in links:
            try:
                href = link.get_attribute('href') or ''
                link_text = (link.inner_text() or '').strip().lower()
                link_class = (link.get_attribute('class') or '').lower()
                aria_label = (link.get_attribute('aria-label') or '').lower()

                combined_text = f"{link_text} {link_class} {aria_label}"

                # Check text content for login keywords
                text_match = any(kw in combined_text for kw in self.LOGIN_PAGE_KEYWORDS)

                # Check href for login keywords
                href_lower = href.lower()
                href_match = any(kw in href_lower for kw in self.LOGIN_HREF_KEYWORDS)

                if text_match or href_match:
                    # Build absolute URL
                    if href.startswith('http'):
                        url = href
                    elif href.startswith('/'):
                        # Relative URL - combine with base
                        from urllib.parse import urljoin
                        url = urljoin(self.url, href)
                    else:
                        continue  # Skip javascript: or other non-http links

                    # Calculate confidence
                    confidence = 0.5
                    reasons = []
                    if text_match:
                        confidence += 0.25
                        reasons.append(f"text matches login keywords: '{link_text[:30]}'")
                    if href_match:
                        confidence += 0.25
                        reasons.append(f"href contains login keyword: '{href[:50]}'")

                    candidates.append((url, confidence, '; '.join(reasons)))

            except Exception:
                continue

        # Search for buttons that might open login modals or navigate to login
        buttons = self.page.locator('button').all()
        for btn in buttons:
            try:
                btn_text = (btn.inner_text() or '').strip().lower()
                btn_class = (btn.get_attribute('class') or '').lower()
                aria_label = (btn.get_attribute('aria-label') or '').lower()
                onclick = (btn.get_attribute('onclick') or '').lower()
                data_href = btn.get_attribute('data-href') or ''

                combined_text = f"{btn_text} {btn_class} {aria_label}"

                if any(kw in combined_text for kw in self.LOGIN_PAGE_KEYWORDS):
                    # Check if button has a data-href or similar
                    if data_href:
                        if data_href.startswith('http'):
                            url = data_href
                        else:
                            from urllib.parse import urljoin
                            url = urljoin(self.url, data_href)
                        candidates.append((url, 0.6, f"button with login text has data-href: '{btn_text[:30]}'"))
                    elif 'login' in onclick or 'signin' in onclick:
                        # Button might trigger navigation via onclick
                        # We can't extract URL from JS, but note it exists
                        print(f"  Found login button with onclick: '{btn_text[:30]}' (cannot extract URL)")

            except Exception:
                continue

        # Search for divs/spans that might be styled as buttons
        clickables = self.page.locator('div[role="button"], span[role="button"], [class*="button"]').all()
        for el in clickables[:50]:  # Limit search
            try:
                el_text = (el.inner_text() or '').strip().lower()
                if len(el_text) > 50:  # Skip elements with too much text
                    continue

                if any(kw in el_text for kw in self.LOGIN_PAGE_KEYWORDS):
                    # Check for data attributes that might contain URLs
                    data_href = el.get_attribute('data-href') or ''
                    onclick = (el.get_attribute('onclick') or '').lower()

                    if data_href:
                        from urllib.parse import urljoin
                        url = urljoin(self.url, data_href) if not data_href.startswith('http') else data_href
                        candidates.append((url, 0.5, f"clickable element with login text: '{el_text[:30]}'"))

            except Exception:
                continue

        if not candidates:
            return None

        # Sort by confidence and return the best match
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_url, best_confidence, best_reason = candidates[0]

        print(f"  Found login page candidate: {best_url}")
        print(f"    Confidence: {best_confidence:.2f}, Reason: {best_reason}")

        return best_url

    def _discover_currency_selectors(self) -> Dict[str, List[SelectorCandidate]]:
        """Discover currency/balance display selectors."""
        selectors = {
            'gold_coins': [],
            'sweeps_coins': [],
            'toggle_dropdown': [],
            'toggle_switch': [],
        }

        # Look for elements containing currency keywords and numbers
        all_elements = self.page.locator('*').all()

        # Number pattern to identify potential balance displays
        number_pattern = re.compile(r'\$?\d{1,3}(,\d{3})*(\.\d{2,})?')

        for el in all_elements[:500]:  # Limit search to prevent timeout
            try:
                el_text = el.inner_text() or ''
                el_class = el.get_attribute('class') or ''
                el_id = el.get_attribute('id') or ''
                el_data_testid = el.get_attribute('data-testid') or ''

                combined = f"{el_text} {el_class} {el_id} {el_data_testid}".lower()

                # Check if element contains a number
                has_number = number_pattern.search(el_text)

                # Check for gold coins
                if has_number and any(kw in combined for kw in self.CURRENCY_KEYWORDS['gold_coins']):
                    selector = self._generate_selector(el)
                    confidence = self._calculate_confidence(combined, self.CURRENCY_KEYWORDS['gold_coins'], has_number)
                    selectors['gold_coins'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found element with gold coin keywords and number"],
                        element_text=el_text[:50]
                    ))

                # Check for sweeps coins
                if has_number and any(kw in combined for kw in self.CURRENCY_KEYWORDS['sweeps_coins']):
                    selector = self._generate_selector(el)
                    confidence = self._calculate_confidence(combined, self.CURRENCY_KEYWORDS['sweeps_coins'], has_number)
                    selectors['sweeps_coins'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found element with sweeps coin keywords and number"],
                        element_text=el_text[:50]
                    ))

                # Check for currency toggle/switch
                if any(word in combined for word in ['currency', 'coin', 'balance', 'toggle', 'switch']):
                    if el.get_attribute('role') in ['button', 'switch'] or el.evaluate('el => el.tagName') == 'BUTTON':
                        selector = self._generate_selector(el)
                        confidence = 0.6
                        selectors['toggle_dropdown'].append(SelectorCandidate(
                            selector=selector,
                            confidence=confidence,
                            element_count=1,
                            reasons=["Found potential currency toggle button"],
                            element_text=el_text[:30]
                        ))

            except Exception:
                continue

        return selectors

    def _discover_claim_selectors(self) -> Dict[str, List[SelectorCandidate]]:
        """Discover bonus claim button and modal selectors."""
        selectors = {
            'modal_opener': [],  # For MTB pattern
            'tab': [],  # For MTB pattern
            'claim_btn': [],
            'enabled_btn': [],  # For generic pattern
            'modal': [],
            'close_btn': [],
        }

        # Find buttons with claim-related keywords
        buttons = self.page.locator('button').all()

        for btn in buttons:
            try:
                btn_text = (btn.inner_text() or '').lower()
                btn_class = btn.get_attribute('class') or ''
                btn_id = btn.get_attribute('id') or ''
                btn_data_testid = btn.get_attribute('data-testid') or ''

                combined = f"{btn_text} {btn_class} {btn_id} {btn_data_testid}".lower()

                # Check for claim buttons
                if any(kw in combined for kw in self.CLAIM_KEYWORDS):
                    selector = self._generate_selector(btn)
                    confidence = self._calculate_confidence(combined, self.CLAIM_KEYWORDS)
                    selectors['claim_btn'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found button with claim keywords"],
                        element_text=btn_text[:30]
                    ))

                # Check for modal openers (wallet, account buttons)
                if any(kw in combined for kw in self.MODAL_KEYWORDS):
                    selector = self._generate_selector(btn)
                    confidence = self._calculate_confidence(combined, self.MODAL_KEYWORDS)
                    selectors['modal_opener'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found potential modal opener button"],
                        element_text=btn_text[:30]
                    ))

                # Check for close buttons
                if any(kw in combined for kw in self.CLOSE_KEYWORDS):
                    selector = self._generate_selector(btn)
                    confidence = self._calculate_confidence(combined, self.CLOSE_KEYWORDS)
                    selectors['close_btn'].append(SelectorCandidate(
                        selector=selector,
                        confidence=confidence,
                        element_count=1,
                        reasons=["Found close button"],
                        element_text=btn_text[:30]
                    ))

            except Exception:
                continue

        # Look for modal containers
        modal_candidates = self.page.locator('[role="dialog"], .modal, .popup, [class*="modal"], [class*="dialog"]').all()
        for modal in modal_candidates[:5]:
            try:
                selector = self._generate_selector(modal)
                selectors['modal'].append(SelectorCandidate(
                    selector=selector,
                    confidence=0.7,
                    element_count=1,
                    reasons=["Found modal/dialog element"],
                ))
            except Exception:
                continue

        return selectors

    def _detect_claim_pattern(self, claim_selectors: Dict[str, List[SelectorCandidate]]) -> Optional[str]:
        """Detect whether the site uses MTB or Generic claim pattern."""
        has_modal_opener = len(claim_selectors.get('modal_opener', [])) > 0
        has_tabs = len(claim_selectors.get('tab', [])) > 0
        has_claim_btn = len(claim_selectors.get('claim_btn', [])) > 0

        # MTB pattern: modal opener + tabs + claim button
        if has_modal_opener and has_claim_btn:
            return 'mtb'

        # Generic pattern: just claim buttons in modals/popups
        if has_claim_btn:
            return 'generic'

        return None

    def _attempt_login(self, username: str, password: str, totp_secret: Optional[str],
                      result: DiscoveryResult) -> bool:
        """Attempt to login using discovered selectors."""
        login_selectors = result.login_selectors

        if not login_selectors.get('username') or not login_selectors.get('password'):
            result.errors.append("Could not find username or password selectors")
            return False

        try:
            # Get best selectors
            username_sel = login_selectors['username'][0].selector
            password_sel = login_selectors['password'][0].selector
            submit_sel = login_selectors['submit'][0].selector if login_selectors.get('submit') else None

            # Fill username
            self.page.fill(username_sel, username, timeout=5000)
            self.page.wait_for_timeout(gaussian_random_delay(1500))

            # Fill password
            self.page.fill(password_sel, password, timeout=5000)
            self.page.wait_for_timeout(gaussian_random_delay(1500))

            # Click submit
            if submit_sel:
                self.page.click(submit_sel, timeout=5000)
            else:
                # Try pressing Enter on password field
                self.page.press(password_sel, 'Enter')

            # Wait for navigation or page change
            self.page.wait_for_timeout(3000)
            wait_for_load_all_safe(self.page, timeout=2000)

            # Check for 2FA
            if totp_secret and login_selectors.get('totp_code'):
                result.requires_2fa = True
                # Import pyotp for TOTP generation
                try:
                    import pyotp
                    totp = pyotp.TOTP(totp_secret)
                    code = totp.now()

                    totp_sel = login_selectors['totp_code'][0].selector
                    totp_submit_sel = login_selectors.get('totp_submit', [{}])[0].selector if login_selectors.get('totp_submit') else None

                    self.page.fill(totp_sel, code, timeout=5000)
                    self.page.wait_for_timeout(gaussian_random_delay())

                    if totp_submit_sel:
                        self.page.click(totp_submit_sel, timeout=5000)
                    else:
                        self.page.press(totp_sel, 'Enter')

                    self.page.wait_for_timeout(3000)
                    wait_for_load_all_safe(self.page, timeout=2000)
                except ImportError:
                    result.errors.append("pyotp not installed - cannot handle 2FA")

            # Check if login was successful (URL changed or login form disappeared)
            current_url = self.page.url
            login_form_visible = self.page.locator(username_sel).count() > 0

            if not login_form_visible or current_url != self.url:
                print(f"Login successful! New URL: {current_url}")
                return True
            else:
                result.errors.append("Login may have failed - login form still visible")
                return False

        except Exception as e:
            result.errors.append(f"Login attempt failed: {str(e)}")
            return False

    def _generate_selector(self, element: ElementHandle) -> str:
        """Generate a CSS selector for an element."""
        try:
            # Try to get data-testid first (most reliable)
            data_testid = element.get_attribute('data-testid')
            if data_testid:
                return f'[data-testid="{data_testid}"]'

            # Try ID
            el_id = element.get_attribute('id')
            if el_id:
                return f'#{el_id}'

            # Try name attribute
            el_name = element.get_attribute('name')
            tag_name = element.evaluate('el => el.tagName').lower()
            if el_name:
                return f'{tag_name}[name="{el_name}"]'

            # Try class-based selector
            el_class = element.get_attribute('class')
            if el_class:
                classes = el_class.strip().split()
                if classes:
                    class_selector = '.' + '.'.join(classes[:2])  # Use first 2 classes
                    return f'{tag_name}{class_selector}'

            # Fall back to tag name (not very specific)
            return tag_name

        except Exception:
            return 'unknown'

    def _calculate_confidence(self, text: str, keywords: List[str], bonus: bool = False) -> float:
        """Calculate confidence score based on keyword matching."""
        matches = sum(1 for kw in keywords if kw in text)
        base_confidence = min(0.5 + (matches * 0.2), 0.95)

        if bonus:
            base_confidence = min(base_confidence + 0.1, 1.0)

        return base_confidence

    def print_results(self, result: DiscoveryResult):
        """Print discovery results in a readable format."""
        print("\n" + "="*80)
        print(f"Casino Selector Discovery Results for: {result.url}")
        print("="*80)

        if result.errors:
            print("\n⚠ ERRORS:")
            for error in result.errors:
                print(f"  - {error}")

        print("\n📝 LOGIN SELECTORS:")
        self._print_selector_section(result.login_selectors)

        print("\n💰 CURRENCY SELECTORS:")
        self._print_selector_section(result.currency_selectors)

        print("\n🎁 CLAIM SELECTORS:")
        self._print_selector_section(result.claim_selectors)

        if result.detected_pattern:
            print(f"\n🎯 Detected Claim Pattern: {result.detected_pattern.upper()}")

        if result.requires_2fa:
            print("\n🔐 Site requires 2FA authentication")

        print("\n" + "="*80)

    def _print_selector_section(self, selectors: Dict[str, List[SelectorCandidate]]):
        """Print a section of selectors."""
        for key, candidates in selectors.items():
            if candidates:
                print(f"\n  {key.upper()}:")
                for i, candidate in enumerate(sorted(candidates, key=lambda x: x.confidence, reverse=True)[:3], 1):
                    print(f"    {i}. {candidate.selector}")
                    print(f"       Confidence: {candidate.confidence:.2f}")
                    print(f"       Reasons: {', '.join(candidate.reasons)}")
                    if candidate.element_text:
                        print(f"       Text: '{candidate.element_text}'")

    def export_to_config_file(self, result: DiscoveryResult, output_file: str):
        """Export discovery result as a Python config file."""
        config = result.to_casino_config()

        code = f'''"""
Auto-generated casino configuration using selector discovery.
Generated for: {result.url}
"""

from casino import (
    CasinoConfig,
    LoginConfig,
    CurrencyDisplayConfig,
    Currency,
    MTBClaimConfig,
    GenericClaimConfig,
    make_casino_automation,
)


def create_config() -> CasinoConfig:
    """Create casino configuration."""

    login_config = LoginConfig(
        username_selector={repr(config.login.username_selector)},
        password_selector={repr(config.login.password_selector)},
        login_submit_selector={repr(config.login.login_submit_selector)},
        totp_code_selector={repr(config.login.totp_code_selector)},
        totp_submit_selector={repr(config.login.totp_submit_selector)},
    )

    currency_display_config = CurrencyDisplayConfig(
        currencies=[
'''

        for currency in config.currency_display.currencies:
            code += f'''            Currency(
                name={repr(currency.name)},
                code={repr(currency.code)},
                selectors={repr(currency.selectors)},
            ),
'''

        code += f'''        ],
        currency_toggle_dropdown_selector={repr(config.currency_display.currency_toggle_dropdown_selector)},
        currency_toggle_switch_selector={repr(config.currency_display.currency_toggle_switch_selector)},
    )

'''

        if config.claim_pattern == 'mtb':
            code += f'''    claim_config = MTBClaimConfig(
        modal_selector={repr(config.claim_config.modal_selector)},
        tab_selector={repr(config.claim_config.tab_selector)},
        btn_selector={repr(config.claim_config.btn_selector)},
        close_btn_selector={repr(config.claim_config.close_btn_selector)},
    )
'''
        elif config.claim_pattern == 'generic':
            code += f'''    claim_config = GenericClaimConfig(
        main_enabled_selector={repr(config.claim_config.main_enabled_selector)},
        modal_selector={repr(config.claim_config.modal_selector)},
        close_modal_selector={repr(config.claim_config.close_modal_selector)},
    )
'''
        else:
            code += '    claim_config = None  # Could not detect claim pattern\n'

        code += f'''
    return CasinoConfig(
        name={repr(config.name)},
        url={repr(config.url)},
        login_url={repr(config.login_url)},
        login=login_config,
        currency_display=currency_display_config,
        claim_config=claim_config,
        claim_pattern={repr(config.claim_pattern)},
        requires_2fa={config.requires_2fa},
    )


if __name__ == "__main__":
    config = create_config()
    main = make_casino_automation(config)
    main()
'''

        with open(output_file, 'w') as f:
            f.write(code)

        print(f"\n✅ Configuration exported to: {output_file}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Discover CSS selectors for casino automation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Passive discovery (no login)
  python casino_selector_discovery.py https://casino-site.com

  # Active discovery with credentials from environment
  python casino_selector_discovery.py https://stake.us --test-login

  # Active discovery with explicit credentials
  python casino_selector_discovery.py https://casino.com --username user --password pass --test-login

  # Export to config file
  python casino_selector_discovery.py https://casino.com --export config_casino.py
        """
    )

    parser.add_argument('url', help='Casino site URL to analyze')
    parser.add_argument('--username', help='Username for login testing')
    parser.add_argument('--password', help='Password for login testing')
    parser.add_argument('--totp-secret', help='TOTP secret for 2FA')
    parser.add_argument('--test-login', action='store_true', help='Actually attempt login')
    parser.add_argument('--headless', action='store_true', default=True, help='Run browser in headless mode')
    parser.add_argument('--no-headless', action='store_false', dest='headless', help='Show browser window')
    parser.add_argument('--timeout', type=int, default=30000, help='Page load timeout in ms')
    parser.add_argument('--export', help='Export results to Python config file')
    parser.add_argument('--json', help='Export results to JSON file')

    # Stealth options
    parser.add_argument('--proxy', help='Proxy server URL (e.g., http://user:pass@host:port)')
    parser.add_argument('--no-stealth', action='store_true', help='Disable stealth mode (use raw Playwright)')
    parser.add_argument('--solve-cloudflare', action='store_true', help='Attempt to solve Cloudflare challenges')
    parser.add_argument('--user-data-dir', help='Browser profile directory for session persistence')
    parser.add_argument('--geoip', action='store_true', help='Spoof location based on proxy IP')
    parser.add_argument('--block-webrtc', action='store_true', help='Block WebRTC to prevent IP leaks')

    args = parser.parse_args()

    # Try to get credentials from environment if not provided
    if args.test_login and not (args.username and args.password):
        try:
            creds: Tuple[str, str, str] = get_credentials(args.url, twofa=bool(args.totp_secret))
            args.username = args.username or creds[0]
            args.password = args.password or creds[1]
            args.totp_secret = args.totp_secret or creds[2]

            if not (args.username and args.password):
                print("⚠ Warning: --test-login specified but credentials not found")
                print(f"Set environment variables: {url_to_env_prefix(args.url)}_USERNAME and _PASSWORD")
        except Exception as e:
            print(f"⚠ Warning: Could not get credentials from environment: {e}")

    # Run discovery
    with CasinoSelectorDiscovery(
        args.url,
        headless=args.headless,
        timeout=args.timeout,
        proxy=args.proxy,
        use_stealth=not args.no_stealth,
        solve_cloudflare=args.solve_cloudflare,
        user_data_dir=args.user_data_dir,
        geoip=args.geoip,
        block_webrtc=args.block_webrtc,
    ) as discovery:
        result = discovery.discover(
            username=args.username,
            password=args.password,
            totp_secret=args.totp_secret,
            test_login=args.test_login,
        )

        # Print results
        discovery.print_results(result)

        # Export if requested
        if args.export:
            discovery.export_to_config_file(result, args.export)

        if args.json:
            # Export raw discovery result as JSON
            json_data = {
                'url': result.url,
                'detected_pattern': result.detected_pattern,
                'requires_2fa': result.requires_2fa,
                'login_selectors': {
                    k: [{'selector': c.selector, 'confidence': c.confidence, 'reasons': c.reasons}
                        for c in v]
                    for k, v in result.login_selectors.items()
                },
                'currency_selectors': {
                    k: [{'selector': c.selector, 'confidence': c.confidence, 'reasons': c.reasons}
                        for c in v]
                    for k, v in result.currency_selectors.items()
                },
                'claim_selectors': {
                    k: [{'selector': c.selector, 'confidence': c.confidence, 'reasons': c.reasons}
                        for c in v]
                    for k, v in result.claim_selectors.items()
                },
                'errors': result.errors,
            }
            with open(args.json, 'w') as f:
                json.dump(json_data, f, indent=2)
            print(f"✅ JSON results exported to: {args.json}")


if __name__ == '__main__':
    main()
