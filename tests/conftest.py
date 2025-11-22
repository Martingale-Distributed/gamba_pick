"""Pytest configuration and shared fixtures for gamba_pick tests."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


# ============================================================================
# Environment and File System Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_env_credentials(monkeypatch):
    """Mock environment variables for casino credentials."""
    credentials = {
        "STAKE_US_USERNAME": "test_user@example.com",
        "STAKE_US_PASSWORD": "test_password_123",
        "STAKE_US_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "LUCKYBIRD_IO_USERNAME": "test_bird@example.com",
        "LUCKYBIRD_IO_PASSWORD": "test_bird_pass",
    }
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    return credentials


@pytest.fixture
def mock_env_no_credentials(monkeypatch):
    """Ensure no credentials are set in environment."""
    env_vars = [
        "STAKE_US_USERNAME",
        "STAKE_US_PASSWORD",
        "STAKE_US_TOTP_SECRET",
        "LUCKYBIRD_IO_USERNAME",
        "LUCKYBIRD_IO_PASSWORD",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)


# ============================================================================
# Mock Playwright Objects
# ============================================================================


@pytest.fixture
def mock_page():
    """Create a mock Playwright Page object."""
    page = AsyncMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Test Page")
    page.content = AsyncMock(return_value="<html><body>Test</body></html>")
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()
    page.press = AsyncMock()
    page.screenshot = AsyncMock()
    page.query_selector = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    page.locator = Mock(return_value=Mock())
    page.evaluate = AsyncMock()
    page.is_visible = AsyncMock(return_value=True)
    page.is_hidden = AsyncMock(return_value=False)
    return page


@pytest.fixture
def mock_element_handle():
    """Create a mock Playwright ElementHandle object."""
    element = AsyncMock()
    element.click = AsyncMock()
    element.fill = AsyncMock()
    element.type = AsyncMock()
    element.inner_text = AsyncMock(return_value="Test Text")
    element.text_content = AsyncMock(return_value="Test Text")
    element.get_attribute = AsyncMock()
    element.is_visible = AsyncMock(return_value=True)
    element.is_enabled = AsyncMock(return_value=True)
    element.bounding_box = AsyncMock(
        return_value={"x": 10, "y": 10, "width": 100, "height": 50}
    )
    return element


@pytest.fixture
def mock_locator():
    """Create a mock Playwright Locator object."""
    locator = Mock()
    locator.click = AsyncMock()
    locator.fill = AsyncMock()
    locator.type = AsyncMock()
    locator.inner_text = AsyncMock(return_value="Test Text")
    locator.text_content = AsyncMock(return_value="Test Text")
    locator.get_attribute = AsyncMock()
    locator.is_visible = AsyncMock(return_value=True)
    locator.is_enabled = AsyncMock(return_value=True)
    locator.count = AsyncMock(return_value=1)
    locator.first = Mock(return_value=locator)
    locator.last = Mock(return_value=locator)
    locator.nth = Mock(return_value=locator)
    return locator


@pytest.fixture
def mock_browser():
    """Create a mock Playwright Browser object."""
    browser = AsyncMock()
    browser.new_context = AsyncMock()
    browser.close = AsyncMock()
    browser.contexts = []
    return browser


@pytest.fixture
def mock_browser_context(mock_page):
    """Create a mock Playwright BrowserContext object."""
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=mock_page)
    context.close = AsyncMock()
    context.pages = [mock_page]
    context.cookies = AsyncMock(return_value=[])
    context.add_cookies = AsyncMock()
    context.clear_cookies = AsyncMock()
    return context


# ============================================================================
# Mock Scrapling Objects
# ============================================================================


@pytest.fixture
def mock_adaptor():
    """Create a mock Scrapling Adaptor object."""
    adaptor = Mock()
    adaptor.page = AsyncMock()
    adaptor.page.url = "https://example.com"
    adaptor.get = AsyncMock()
    adaptor.post = AsyncMock()
    adaptor.find = Mock(return_value=Mock())
    adaptor.find_all = Mock(return_value=[])
    adaptor.text = "Mock page text"
    adaptor.html = "<html><body>Mock</body></html>"
    adaptor.close = AsyncMock()
    return adaptor


# ============================================================================
# Casino Configuration Fixtures
# ============================================================================


@pytest.fixture
def mock_casino_account_state():
    """Create a mock CasinoAccountState object."""
    from casino import CasinoAccountState

    return CasinoAccountState(
        username="test_user@example.com",
        currency_balances={"SC": 100.50, "GC": 1000.00},
        screenshot_path="/tmp/test_screenshot.png",
        raw_balances_html="<div>SC: 100.50</div>",
    )


@pytest.fixture
def mock_currency_config():
    """Create a mock Currency configuration."""
    from casino import Currency

    return Currency(
        symbol="SC",
        dropdown_toggle_selector="button.currency-toggle",
        dropdown_item_text="Sweeps",
        balance_selector="span.balance",
    )


@pytest.fixture
def mock_login_config():
    """Create a mock LoginConfig."""
    from casino import LoginConfig

    return LoginConfig(
        login_url="https://test-casino.com/login",
        username_selector="input#email",
        password_selector="input#password",
        submit_selector="button[type='submit']",
        logged_in_url_contains="/account",
        totp_selector="input#totp",
    )


# ============================================================================
# Faucet/Pick Fixtures
# ============================================================================


@pytest.fixture
def mock_account_state():
    """Create a mock faucet AccountState object."""
    from scrapling_pick import AccountState

    return AccountState(
        balance=1000.50,
        wagered=500.25,
        target=1000.00,
        bonus_claiming_remaining=3,
    )


@pytest.fixture
def mock_pick_data():
    """Create mock Pick data for testing."""
    from datetime import datetime, timedelta
    from scrapling_pick import Pick

    return Pick(
        user="test_user@example.com",
        pick_number=12345,
        timestamp=datetime.now(),
        balance_before=1000.0,
        balance_after=1050.0,
        wagered_before=500.0,
        wagered_after=550.0,
        claimed_bonus=True,
        error=None,
    )


@pytest.fixture
def sample_picks_json(temp_dir):
    """Create a sample picks JSON file for testing."""
    picks_data = [
        {
            "user": "test_user@example.com",
            "pick_number": 1,
            "timestamp": "2025-01-01T12:00:00",
            "balance_before": 1000.0,
            "balance_after": 1050.0,
            "wagered_before": 500.0,
            "wagered_after": 550.0,
            "claimed_bonus": True,
            "error": None,
        },
        {
            "user": "test_user2@example.com",
            "pick_number": 2,
            "timestamp": "2025-01-01T13:00:00",
            "balance_before": 2000.0,
            "balance_after": 2100.0,
            "wagered_before": 1000.0,
            "wagered_after": 1100.0,
            "claimed_bonus": False,
            "error": "Timeout",
        },
    ]

    picks_file = temp_dir / "picks.json"
    with open(picks_file, "w") as f:
        json.dump(picks_data, f)

    return picks_file


# ============================================================================
# HTML Mock Fixtures
# ============================================================================


@pytest.fixture
def mock_balance_html():
    """Mock HTML containing balance information."""
    return """
    <html>
        <body>
            <div class="balance-container">
                <span class="currency-symbol">SC</span>
                <span class="balance">100.50</span>
            </div>
            <div class="balance-container">
                <span class="currency-symbol">GC</span>
                <span class="balance">1,000.00</span>
            </div>
        </body>
    </html>
    """


@pytest.fixture
def mock_login_page_html():
    """Mock HTML for a login page."""
    return """
    <html>
        <body>
            <form id="login-form">
                <input type="email" id="email" name="email" />
                <input type="password" id="password" name="password" />
                <input type="text" id="totp" name="totp" />
                <button type="submit">Login</button>
            </form>
        </body>
    </html>
    """


@pytest.fixture
def mock_claim_modal_html():
    """Mock HTML for a claim modal."""
    return """
    <html>
        <body>
            <div class="modal" id="claim-modal">
                <div class="modal-tabs">
                    <button class="tab" data-tab="daily">Daily Bonus</button>
                    <button class="tab" data-tab="weekly">Weekly Bonus</button>
                </div>
                <div class="modal-content">
                    <button class="claim-button">Claim Now</button>
                </div>
                <button class="modal-close">×</button>
            </div>
        </body>
    </html>
    """


# ============================================================================
# Time and Random Mocking
# ============================================================================


@pytest.fixture
def fixed_random(monkeypatch):
    """Fix random number generation for deterministic tests."""
    import random

    random.seed(42)
    yield
    random.seed()


@pytest.fixture
def mock_time(monkeypatch):
    """Mock time.time() to return a fixed value."""
    import time

    fixed_time = 1704110400.0  # 2024-01-01 12:00:00 UTC
    monkeypatch.setattr(time, "time", lambda: fixed_time)
    return fixed_time


# ============================================================================
# Argument Parser Fixtures
# ============================================================================


@pytest.fixture
def mock_args_casino():
    """Mock parsed arguments for casino scripts."""
    args = Mock()
    args.headless = True
    args.proxy = None
    args.skip_claim = False
    args.user_data_dir = None
    args.timeout = 30
    return args


@pytest.fixture
def mock_args_faucet():
    """Mock parsed arguments for faucet scripts."""
    args = Mock()
    args.headless = True
    args.proxy = None
    args.play_keno = False
    args.summarize = False
    args.enable_screenshots = False
    args.only = None
    args.skip = None
    args.user_data_dir = None
    return args
