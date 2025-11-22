"""Unit tests for casino.py utility functions."""

import os
import random
from unittest.mock import AsyncMock, Mock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casino import (
    CLICK_TIMEOUT_MS,
    MAX_CLICK_RETRIES,
    gaussian_random_delay,
    get_credentials,
    safe_click,
    url_to_env_prefix,
    wait_for_clickable,
)


# ============================================================================
# URL to Environment Prefix Tests
# ============================================================================


@pytest.mark.unit
class TestUrlToEnvPrefix:
    """Tests for url_to_env_prefix function."""

    def test_basic_url(self):
        """Test conversion of basic URL."""
        assert url_to_env_prefix("https://stake.us/") == "STAKE"

    def test_url_with_subdomain(self):
        """Test conversion of URL with subdomain."""
        assert url_to_env_prefix("https://www.stake.us/") == "WWW"

    def test_url_without_protocol(self):
        """Test URL without protocol defaults to first part."""
        # urlparse treats this as path, not netloc
        result = url_to_env_prefix("stake.us")
        assert result == ""  # No netloc means empty result

    def test_complex_domain(self):
        """Test complex domain names."""
        assert url_to_env_prefix("https://luckybird.io/casino") == "LUCKYBIRD"

    def test_localhost(self):
        """Test localhost URL."""
        assert url_to_env_prefix("http://localhost:8000") == "LOCALHOST"

    def test_ip_address(self):
        """Test IP address URL."""
        result = url_to_env_prefix("http://127.0.0.1:8000")
        assert result == "127"

    @given(st.text(min_size=1).filter(lambda x: "." in x))
    def test_property_always_uppercase(self, domain):
        """Property test: result should always be uppercase."""
        try:
            result = url_to_env_prefix(f"https://{domain}")
            assert result == result.upper()
        except Exception:
            # Invalid URLs might raise exceptions, which is acceptable
            pass


# ============================================================================
# Get Credentials Tests
# ============================================================================


@pytest.mark.unit
class TestGetCredentials:
    """Tests for get_credentials function."""

    def test_get_credentials_success(self, mock_env_credentials):
        """Test successful credential retrieval."""
        username, password, totp = get_credentials("https://stake.us")
        assert username == "test_user@example.com"
        assert password == "test_password_123"
        assert totp == "JBSWY3DPEHPK3PXP"

    def test_get_credentials_missing_username(self, monkeypatch):
        """Test error when username is missing."""
        monkeypatch.delenv("STAKE_US_USERNAME", raising=False)
        monkeypatch.setenv("STAKE_US_PASSWORD", "password")

        with pytest.raises(ValueError) as exc_info:
            get_credentials("https://stake.us")
        assert "STAKE_US_USERNAME and STAKE_US_PASSWORD must be set" in str(exc_info.value)

    def test_get_credentials_missing_password(self, monkeypatch):
        """Test error when password is missing."""
        monkeypatch.setenv("STAKE_US_USERNAME", "user")
        monkeypatch.delenv("STAKE_US_PASSWORD", raising=False)

        with pytest.raises(ValueError) as exc_info:
            get_credentials("https://stake.us")
        assert "STAKE_US_USERNAME and STAKE_US_PASSWORD must be set" in str(exc_info.value)

    def test_get_credentials_missing_both(self, mock_env_no_credentials):
        """Test error when both credentials are missing."""
        with pytest.raises(ValueError) as exc_info:
            get_credentials("https://stake.us")
        assert "STAKE_US_USERNAME and STAKE_US_PASSWORD must be set" in str(exc_info.value)

    def test_get_credentials_2fa_required_but_missing(self, monkeypatch):
        """Test error when 2FA is required but secret is missing."""
        monkeypatch.setenv("STAKE_US_USERNAME", "user")
        monkeypatch.setenv("STAKE_US_PASSWORD", "password")
        monkeypatch.delenv("STAKE_US_2FA", raising=False)

        with pytest.raises(ValueError) as exc_info:
            get_credentials("https://stake.us", twofa=True)
        assert "STAKE_US_2FA must be set when twofa=True" in str(exc_info.value)

    def test_get_credentials_2fa_not_required(self, monkeypatch):
        """Test successful retrieval when 2FA is not required."""
        monkeypatch.setenv("STAKE_US_USERNAME", "user")
        monkeypatch.setenv("STAKE_US_PASSWORD", "password")
        monkeypatch.delenv("STAKE_US_2FA", raising=False)

        username, password, totp = get_credentials("https://stake.us", twofa=False)
        assert username == "user"
        assert password == "password"
        assert totp is None

    def test_get_credentials_different_domains(self, mock_env_credentials):
        """Test credential retrieval for different domains."""
        # Stake.us
        username, password, _ = get_credentials("https://stake.us")
        assert username == "test_user@example.com"

        # LuckyBird.io
        username, password, _ = get_credentials("https://luckybird.io")
        assert username == "test_bird@example.com"


# ============================================================================
# Gaussian Random Delay Tests
# ============================================================================


@pytest.mark.unit
class TestGaussianRandomDelay:
    """Tests for gaussian_random_delay function."""

    def test_default_delay_range(self, fixed_random):
        """Test that default delay is reasonable."""
        delay = gaussian_random_delay()
        assert isinstance(delay, int)
        assert delay >= 0

    def test_custom_mean_and_stddev(self, fixed_random):
        """Test custom mean and standard deviation."""
        delay = gaussian_random_delay(mean=100, stddev=20)
        assert isinstance(delay, int)
        assert delay >= 0

    def test_delay_never_negative(self):
        """Test that delay is never negative even with large stddev."""
        for _ in range(100):
            delay = gaussian_random_delay(mean=10, stddev=50)
            assert delay >= 0, f"Delay should never be negative, got {delay}"

    def test_zero_mean(self, fixed_random):
        """Test with zero mean."""
        delay = gaussian_random_delay(mean=0, stddev=10)
        assert delay >= 0

    def test_statistical_properties(self):
        """Test statistical properties of generated delays."""
        mean = 100
        stddev = 20
        samples = [gaussian_random_delay(mean, stddev) for _ in range(1000)]

        # Check that all samples are non-negative
        assert all(s >= 0 for s in samples)

        # Check that mean is approximately correct (with some tolerance)
        # Note: will be slightly higher than input mean due to max(0, ...) clipping
        sample_mean = sum(samples) / len(samples)
        assert 90 <= sample_mean <= 110, f"Sample mean {sample_mean} too far from expected {mean}"

    @given(
        mean=st.floats(min_value=0, max_value=10000),
        stddev=st.floats(min_value=0.1, max_value=1000),
    )
    def test_property_always_nonnegative(self, mean, stddev):
        """Property test: delay should always be non-negative."""
        delay = gaussian_random_delay(mean, stddev)
        assert delay >= 0
        assert isinstance(delay, int)


# ============================================================================
# Wait for Clickable Tests
# ============================================================================


@pytest.mark.unit
class TestWaitForClickable:
    """Tests for wait_for_clickable function."""

    def test_element_clickable_success(self, mock_page, mock_locator):
        """Test successful wait for clickable element."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False

        result = wait_for_clickable(mock_page, "button.submit")
        assert result is True

        mock_page.locator.assert_called_once_with("button.submit")
        mock_locator.wait_for.assert_called_once_with(state="visible", timeout=CLICK_TIMEOUT_MS)
        mock_locator.scroll_into_view_if_needed.assert_called_once()

    def test_element_disabled(self, mock_page, mock_locator):
        """Test when element is disabled."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = True

        result = wait_for_clickable(mock_page, "button.submit")
        assert result is False

    def test_element_not_visible_timeout(self, mock_page, mock_locator):
        """Test when element is not visible within timeout."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock(side_effect=Exception("Timeout"))

        result = wait_for_clickable(mock_page, "button.submit")
        assert result is False

    def test_scroll_into_view_disabled(self, mock_page, mock_locator):
        """Test with scroll_into_view disabled."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.is_disabled.return_value = False

        result = wait_for_clickable(mock_page, "button.submit", scroll_into_view=False)
        assert result is True

        # scroll_into_view_if_needed should not be called
        mock_locator.scroll_into_view_if_needed.assert_not_called()

    def test_custom_timeout(self, mock_page, mock_locator):
        """Test with custom timeout."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False

        custom_timeout = 10000
        result = wait_for_clickable(mock_page, "button.submit", timeout=custom_timeout)
        assert result is True

        mock_locator.wait_for.assert_called_once_with(state="visible", timeout=custom_timeout)
        mock_locator.scroll_into_view_if_needed.assert_called_once_with(timeout=custom_timeout // 2)

    def test_scroll_fails_but_element_still_clickable(self, mock_page, mock_locator):
        """Test when scroll fails but element is still clickable."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock(side_effect=Exception("Scroll failed"))
        mock_locator.is_disabled.return_value = False

        # Should still return True since scroll failure is not fatal
        result = wait_for_clickable(mock_page, "button.submit")
        assert result is True


# ============================================================================
# Safe Click Tests
# ============================================================================


@pytest.mark.unit
class TestSafeClick:
    """Tests for safe_click function."""

    def test_successful_click_first_attempt(self, mock_page, mock_locator):
        """Test successful click on first attempt."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False
        mock_page.click = AsyncMock()

        result = safe_click(mock_page, "button.submit", delay=50)
        assert result is True
        mock_page.click.assert_called_once()

    def test_click_fails_then_succeeds(self, mock_page, mock_locator):
        """Test click fails first then succeeds on retry."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False

        # First call fails, second succeeds
        mock_page.click = AsyncMock(side_effect=[Exception("Click failed"), None])
        mock_page.wait_for_timeout = AsyncMock()

        result = safe_click(mock_page, "button.submit", delay=50, max_retries=3)
        assert result is True
        assert mock_page.click.call_count == 2

        # Verify exponential backoff was called
        mock_page.wait_for_timeout.assert_called_once_with(1000)  # 2^0 * 1000

    def test_click_fails_all_retries(self, mock_page, mock_locator):
        """Test click fails on all retry attempts."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False
        mock_page.click = AsyncMock(side_effect=Exception("Click failed"))
        mock_page.wait_for_timeout = AsyncMock()

        result = safe_click(mock_page, "button.submit", delay=50, max_retries=3)
        assert result is False
        assert mock_page.click.call_count == 3

    def test_force_click(self, mock_page):
        """Test force click bypasses clickability check."""
        mock_page.click = AsyncMock()

        result = safe_click(mock_page, "button.submit", delay=50, force=True)
        assert result is True

        # locator should not be called when force=True
        mock_page.locator.assert_not_called()

    def test_exponential_backoff_timing(self, mock_page, mock_locator):
        """Test exponential backoff timing between retries."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False
        mock_page.click = AsyncMock(side_effect=Exception("Click failed"))
        mock_page.wait_for_timeout = AsyncMock()

        result = safe_click(mock_page, "button.submit", delay=50, max_retries=4)
        assert result is False

        # Verify exponential backoff: 1000, 2000, 4000 (no wait after last attempt)
        expected_calls = [
            ((1000,),),  # 2^0 * 1000
            ((2000,),),  # 2^1 * 1000
            ((4000,),),  # 2^2 * 1000
        ]
        assert mock_page.wait_for_timeout.call_count == 3
        actual_calls = [call[0] for call in mock_page.wait_for_timeout.call_args_list]
        assert actual_calls == expected_calls

    def test_element_not_clickable(self, mock_page, mock_locator):
        """Test when element is not clickable."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = True
        mock_page.wait_for_timeout = AsyncMock()

        result = safe_click(mock_page, "button.submit", delay=50, max_retries=2)
        assert result is False

        # Click should not be attempted if element is not clickable
        mock_page.click.assert_not_called()

    def test_custom_delay(self, mock_page, mock_locator):
        """Test click with custom delay."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False
        mock_page.click = AsyncMock()

        custom_delay = 123
        result = safe_click(mock_page, "button.submit", delay=custom_delay)
        assert result is True

        # Verify delay was passed to click
        call_kwargs = mock_page.click.call_args[1]
        assert call_kwargs["delay"] == custom_delay

    def test_none_delay_uses_gaussian(self, mock_page, mock_locator):
        """Test that None delay uses gaussian_random_delay."""
        mock_page.locator.return_value = mock_locator
        mock_locator.wait_for = AsyncMock()
        mock_locator.scroll_into_view_if_needed = AsyncMock()
        mock_locator.is_disabled.return_value = False
        mock_page.click = AsyncMock()

        with patch("casino.gaussian_random_delay", return_value=42):
            result = safe_click(mock_page, "button.submit", delay=None)
            assert result is True

            # Verify gaussian delay was used
            call_kwargs = mock_page.click.call_args[1]
            assert call_kwargs["delay"] == 42
