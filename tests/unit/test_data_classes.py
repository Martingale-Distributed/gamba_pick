"""Unit tests for data classes in casino.py and scrapling_pick.py."""

from datetime import datetime

import pytest

from casino import (
    CasinoAccountState,
    Currency,
    CurrencyDisplayConfig,
    LoginConfig,
)
from scrapling_pick import AccountState, Pick


# ============================================================================
# CasinoAccountState Tests
# ============================================================================


@pytest.mark.unit
class TestCasinoAccountState:
    """Tests for CasinoAccountState data class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        state = CasinoAccountState()

        assert state.sweeps_coins == 0.0
        assert state.gold_coins == 0.0
        assert state.vip_level == "None"
        assert state.vip_progress is None

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        state = CasinoAccountState(
            sweeps_coins=100.50,
            gold_coins=2500.75,
            vip_level="Gold",
            vip_progress=0.65,
        )

        assert state.sweeps_coins == 100.50
        assert state.gold_coins == 2500.75
        assert state.vip_level == "Gold"
        assert state.vip_progress == 0.65

    def test_str_representation(self):
        """Test string representation."""
        state = CasinoAccountState(
            sweeps_coins=100.50,
            gold_coins=2500.75,
            vip_level="Gold",
        )

        result = str(state)

        assert "SC: 100.50" in result
        assert "GC: 2500.75" in result
        assert "VIP: Gold" in result

    def test_str_formats_floats(self):
        """Test that string representation formats floats to 2 decimal places."""
        state = CasinoAccountState(
            sweeps_coins=100.123456,
            gold_coins=2500.987654,
        )

        result = str(state)

        assert "100.12" in result
        assert "2500.99" in result

    def test_vip_progress_optional(self):
        """Test that vip_progress can be None."""
        state = CasinoAccountState(vip_progress=None)

        assert state.vip_progress is None


# ============================================================================
# Currency Tests
# ============================================================================


@pytest.mark.unit
class TestCurrency:
    """Tests for Currency class."""

    def test_init_basic(self):
        """Test basic initialization."""
        currency = Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=["span.sweeps-balance", "div.sc-balance"],
        )

        assert currency.name == "Sweeps Coins"
        assert currency.code == "SC"
        assert len(currency.selectors) == 2
        assert "span.sweeps-balance" in currency.selectors
        assert currency.is_active_selector is None
        assert currency.activate_selector is None

    def test_init_with_optional_selectors(self):
        """Test initialization with optional selectors."""
        currency = Currency(
            name="Gold Coins",
            code="GC",
            selectors=["span.gold-balance"],
            is_active_selector="button.gc-tab.active",
            activate_selector="button.gc-tab",
        )

        assert currency.is_active_selector == "button.gc-tab.active"
        assert currency.activate_selector == "button.gc-tab"

    def test_selectors_list(self):
        """Test that selectors can be a list of multiple selectors."""
        selectors = ["selector1", "selector2", "selector3"]
        currency = Currency(name="Test", code="TEST", selectors=selectors)

        assert currency.selectors == selectors
        assert len(currency.selectors) == 3


# ============================================================================
# CurrencyDisplayConfig Tests
# ============================================================================


@pytest.mark.unit
class TestCurrencyDisplayConfig:
    """Tests for CurrencyDisplayConfig class."""

    def test_init_basic(self):
        """Test basic initialization."""
        sc = Currency(name="Sweeps Coins", code="SC", selectors=["span.sc"])
        gc = Currency(name="Gold Coins", code="GC", selectors=["span.gc"])

        config = CurrencyDisplayConfig(currencies=[sc, gc])

        assert len(config.currencies) == 2
        assert config.currency_toggle_dropdown_selector is None
        assert config.currency_toggle_switch_selector is None

    def test_init_with_dropdown(self):
        """Test initialization with dropdown toggle."""
        sc = Currency(name="Sweeps Coins", code="SC", selectors=["span.sc"])

        config = CurrencyDisplayConfig(
            currencies=[sc],
            currency_toggle_dropdown_selector="button.currency-dropdown",
        )

        assert config.currency_toggle_dropdown_selector == "button.currency-dropdown"

    def test_init_with_switch(self):
        """Test initialization with switch toggle."""
        sc = Currency(name="Sweeps Coins", code="SC", selectors=["span.sc"])

        config = CurrencyDisplayConfig(
            currencies=[sc],
            currency_toggle_switch_selector="div.currency-switch",
        )

        assert config.currency_toggle_switch_selector == "div.currency-switch"

    def test_multiple_currencies(self):
        """Test with multiple currencies."""
        currencies = [
            Currency(name=f"Coin{i}", code=f"C{i}", selectors=[f"span.c{i}"])
            for i in range(5)
        ]

        config = CurrencyDisplayConfig(currencies=currencies)

        assert len(config.currencies) == 5
        for i, currency in enumerate(config.currencies):
            assert currency.name == f"Coin{i}"
            assert currency.code == f"C{i}"


# ============================================================================
# LoginConfig Tests
# ============================================================================


@pytest.mark.unit
class TestLoginConfig:
    """Tests for LoginConfig class."""

    def test_init_basic_required_fields(self):
        """Test initialization with only required fields."""
        config = LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
        )

        assert config.login_url == "https://test-casino.com/login"
        assert config.username_selector == "input#email"
        assert config.password_selector == "input#password"
        assert config.submit_selector == "button[type='submit']"
        assert config.logged_in_url_contains == "/account"

    def test_init_with_totp(self):
        """Test initialization with TOTP selector."""
        config = LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
            totp_selector="input#totp",
        )

        assert config.totp_selector == "input#totp"

    def test_selectors_are_strings(self):
        """Test that all selectors are strings."""
        config = LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
            totp_selector="input#totp",
        )

        assert isinstance(config.login_url, str)
        assert isinstance(config.username_selector, str)
        assert isinstance(config.password_selector, str)
        assert isinstance(config.submit_selector, str)
        assert isinstance(config.logged_in_url_contains, str)
        assert isinstance(config.totp_selector, str)


# ============================================================================
# AccountState (Faucet) Tests
# ============================================================================


@pytest.mark.unit
class TestAccountState:
    """Tests for faucet AccountState data class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        state = AccountState()

        assert state.balance == 0.0
        assert state.wagered == 0.0
        assert state.target == 0.0
        assert state.bonus_claiming_remaining == 0

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        state = AccountState(
            balance=1000.50,
            wagered=500.25,
            target=1500.00,
            bonus_claiming_remaining=3,
        )

        assert state.balance == 1000.50
        assert state.wagered == 500.25
        assert state.target == 1500.00
        assert state.bonus_claiming_remaining == 3

    def test_progress_calculation(self):
        """Test wagering progress calculation."""
        state = AccountState(
            balance=1000.0,
            wagered=750.0,
            target=1000.0,
        )

        # Progress = wagered / target = 750 / 1000 = 0.75 (75%)
        expected_progress = 750.0 / 1000.0
        assert state.wagered / state.target == expected_progress

    def test_remaining_calculation(self):
        """Test remaining wagering calculation."""
        state = AccountState(
            wagered=750.0,
            target=1000.0,
        )

        remaining = state.target - state.wagered
        assert remaining == 250.0


# ============================================================================
# Pick Tests
# ============================================================================


@pytest.mark.unit
class TestPick:
    """Tests for Pick class."""

    def test_init_basic(self):
        """Test basic initialization."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        assert pick.url == "https://tronpick.io"
        assert pick.currency == "TRX"
        assert pick.history == []
        assert pick.last_update is None
        assert pick.balance == 0.0
        assert pick.wagered == 0.0
        assert pick.target == 0.0
        assert pick.remaining_claims == 0
        assert pick.free_spins == 0
        assert pick.cooldown_timer is None

    def test_history_initialization(self):
        """Test that history is initialized as empty list."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        assert isinstance(pick.history, list)
        assert len(pick.history) == 0

    def test_add_history_entry(self):
        """Test adding entries to history."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        dt = datetime(2025, 1, 1, 12, 0, 0)
        pick.history.append((dt, 100.0, 50.0, 150.0, 3, 5))

        assert len(pick.history) == 1
        assert pick.history[0] == (dt, 100.0, 50.0, 150.0, 3, 5)

    def test_multiple_history_entries(self):
        """Test multiple history entries."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        entries = [
            (datetime(2025, 1, 1, 12, 0, 0), 100.0, 50.0, 150.0, 3, 5),
            (datetime(2025, 1, 2, 12, 0, 0), 110.0, 60.0, 150.0, 2, 4),
            (datetime(2025, 1, 3, 12, 0, 0), 120.0, 70.0, 150.0, 1, 3),
        ]

        for entry in entries:
            pick.history.append(entry)

        assert len(pick.history) == 3
        assert pick.history == entries

    def test_str_representation_no_update(self):
        """Test string representation when last_update is None."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        result = str(pick)

        assert "https://tronpick.io" in result
        assert "TRX" in result
        assert "N/A" in result

    def test_update_balance_and_wagered(self):
        """Test updating balance and wagered amounts."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        pick.balance = 100.50
        pick.wagered = 50.25
        pick.last_update = datetime(2025, 1, 1, 12, 0, 0)

        assert pick.balance == 100.50
        assert pick.wagered == 50.25
        assert pick.last_update == datetime(2025, 1, 1, 12, 0, 0)

    def test_cooldown_timer(self):
        """Test cooldown timer attribute."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        pick.cooldown_timer = "05:30"

        assert pick.cooldown_timer == "05:30"

    def test_remaining_claims_and_free_spins(self):
        """Test remaining claims and free spins attributes."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        pick.remaining_claims = 5
        pick.free_spins = 10

        assert pick.remaining_claims == 5
        assert pick.free_spins == 10

    def test_target_tracking(self):
        """Test target amount tracking."""
        pick = Pick(url="https://tronpick.io", currency="TRX")

        pick.target = 1000.0
        pick.wagered = 750.0

        remaining = pick.target - pick.wagered
        assert remaining == 250.0


# ============================================================================
# Data Class Edge Cases
# ============================================================================


@pytest.mark.unit
class TestDataClassEdgeCases:
    """Tests for edge cases in data classes."""

    def test_casino_account_state_zero_values(self):
        """Test CasinoAccountState with zero values."""
        state = CasinoAccountState(sweeps_coins=0.0, gold_coins=0.0)

        assert state.sweeps_coins == 0.0
        assert state.gold_coins == 0.0

    def test_casino_account_state_negative_progress(self):
        """Test CasinoAccountState with negative VIP progress (invalid but possible)."""
        state = CasinoAccountState(vip_progress=-0.5)

        assert state.vip_progress == -0.5

    def test_casino_account_state_progress_over_one(self):
        """Test CasinoAccountState with VIP progress over 1.0 (invalid but possible)."""
        state = CasinoAccountState(vip_progress=1.5)

        assert state.vip_progress == 1.5

    def test_pick_empty_url(self):
        """Test Pick with empty URL."""
        pick = Pick(url="", currency="TRX")

        assert pick.url == ""

    def test_pick_empty_currency(self):
        """Test Pick with empty currency."""
        pick = Pick(url="https://test.com", currency="")

        assert pick.currency == ""

    def test_account_state_negative_balance(self):
        """Test AccountState with negative balance (edge case)."""
        state = AccountState(balance=-10.0)

        assert state.balance == -10.0

    def test_account_state_negative_remaining_claims(self):
        """Test AccountState with negative remaining claims (edge case)."""
        state = AccountState(bonus_claiming_remaining=-1)

        assert state.bonus_claiming_remaining == -1
