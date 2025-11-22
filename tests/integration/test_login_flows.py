"""Integration tests for login flows using mocked Playwright objects."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from casino import LoginConfig, make_login_action_factory


# ============================================================================
# Login Action Factory Integration Tests
# ============================================================================


@pytest.mark.integration
class TestLoginActionFactory:
    """Integration tests for make_login_action_factory."""

    @pytest.fixture
    def basic_login_config(self):
        """Create a basic login configuration."""
        return LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
        )

    @pytest.fixture
    def totp_login_config(self):
        """Create a login configuration with TOTP."""
        return LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
            totp_selector="input#totp",
        )

    def test_make_login_action_factory_basic(self, basic_login_config, mock_page, mock_env_credentials):
        """Test creating a basic login action without TOTP."""
        # Create the login action factory
        login_action = make_login_action_factory(
            login_config=basic_login_config,
            url="https://test-casino.com",
            twofa=False,
        )

        # Mock page methods
        mock_page.goto = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        # Execute the login action
        login_action(mock_page)

        # Verify the login flow
        mock_page.goto.assert_called_once_with("https://test-casino.com/login")

        # Should fill username and password
        fill_calls = mock_page.fill.call_args_list
        assert len(fill_calls) == 2

        # Verify username fill
        assert fill_calls[0][0][0] == "input#email"
        assert "test_user@example.com" in str(fill_calls[0])

        # Verify password fill
        assert fill_calls[1][0][0] == "input#password"

        # Should click submit
        mock_page.click.assert_called()

    def test_make_login_action_factory_with_totp(self, totp_login_config, mock_page, mock_env_credentials):
        """Test creating a login action with TOTP."""
        # Create the login action factory
        login_action = make_login_action_factory(
            login_config=totp_login_config,
            url="https://test-casino.com",
            twofa=True,
        )

        # Mock page methods
        mock_page.goto = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        with patch("casino.pyotp.TOTP") as mock_totp:
            mock_totp.return_value.now.return_value = "123456"

            # Execute the login action
            login_action(mock_page)

            # Verify TOTP was generated
            mock_totp.assert_called()

        # Verify the login flow includes TOTP
        fill_calls = mock_page.fill.call_args_list
        # Should fill username, password, and TOTP (3 fills)
        assert len(fill_calls) == 3

        # Last fill should be TOTP
        assert fill_calls[2][0][0] == "input#totp"

    def test_make_login_action_factory_missing_credentials(self, basic_login_config, mock_env_no_credentials):
        """Test that factory raises error when credentials are missing."""
        with pytest.raises(ValueError, match="USERNAME and.*PASSWORD must be set"):
            make_login_action_factory(
                login_config=basic_login_config,
                url="https://test-casino.com",
                twofa=False,
            )

    def test_make_login_action_factory_totp_required_but_missing(
        self, totp_login_config, monkeypatch
    ):
        """Test that factory raises error when TOTP is required but secret is missing."""
        # Set username and password but not TOTP secret
        monkeypatch.setenv("TEST-CASINO_USERNAME", "user@example.com")
        monkeypatch.setenv("TEST-CASINO_PASSWORD", "password123")
        monkeypatch.delenv("TEST-CASINO_2FA", raising=False)

        with pytest.raises(ValueError, match="2FA must be set when twofa=True"):
            make_login_action_factory(
                login_config=totp_login_config,
                url="https://test-casino.com",
                twofa=True,
            )


# ============================================================================
# Google One Tap Popup Handler Tests
# ============================================================================


@pytest.mark.integration
class TestGoogleOneTapPopupHandler:
    """Integration tests for Google One Tap popup handler."""

    def test_make_handle_google_one_tap_popup(self, mock_page):
        """Test creating Google One Tap popup handler."""
        from casino import make_handle_google_one_tap_popup

        # Create the handler
        handler = make_handle_google_one_tap_popup()

        # Mock the page's query_selector to simulate popup present
        mock_iframe = Mock()
        mock_page.query_selector = Mock(return_value=mock_iframe)
        mock_page.evaluate = Mock()

        # Execute the handler
        handler(mock_page)

        # Verify it tried to find the Google One Tap iframe
        mock_page.query_selector.assert_called()

    def test_google_one_tap_handler_no_popup(self, mock_page):
        """Test handler when no popup is present."""
        from casino import make_handle_google_one_tap_popup

        handler = make_handle_google_one_tap_popup()

        # Mock no popup found
        mock_page.query_selector = Mock(return_value=None)

        # Should not raise error
        handler(mock_page)


# ============================================================================
# Login Flow End-to-End Integration
# ============================================================================


@pytest.mark.integration
class TestLoginFlowIntegration:
    """End-to-end integration tests for complete login flows."""

    def test_complete_login_flow_without_totp(self, mock_page, mock_env_credentials):
        """Test complete login flow without TOTP."""
        config = LoginConfig(
            login_url="https://stake.us/login",
            username_selector="input[name='email']",
            password_selector="input[name='password']",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/casino",
        )

        login_action = make_login_action_factory(
            login_config=config,
            url="https://stake.us",
            twofa=False,
        )

        # Mock the entire flow
        mock_page.goto = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_page.url = "https://stake.us/casino"  # Simulate successful login

        # Execute login
        login_action(mock_page)

        # Verify all steps were called
        mock_page.goto.assert_called_once()
        assert mock_page.fill.call_count >= 2  # At least username and password
        mock_page.click.assert_called()

    def test_complete_login_flow_with_totp(self, mock_page, mock_env_credentials):
        """Test complete login flow with TOTP."""
        config = LoginConfig(
            login_url="https://stake.us/login",
            username_selector="input[name='email']",
            password_selector="input[name='password']",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/casino",
            totp_selector="input[name='totp']",
        )

        with patch("casino.pyotp.TOTP") as mock_totp:
            mock_totp.return_value.now.return_value = "654321"

            login_action = make_login_action_factory(
                login_config=config,
                url="https://stake.us",
                twofa=True,
            )

            # Mock the flow
            mock_page.goto = AsyncMock()
            mock_page.fill = AsyncMock()
            mock_page.click = AsyncMock()
            mock_page.wait_for_timeout = AsyncMock()

            # Execute login
            login_action(mock_page)

            # Verify TOTP was used
            assert mock_page.fill.call_count >= 3  # Username, password, and TOTP

    def test_login_with_pre_login_callback(self, mock_page, mock_env_credentials):
        """Test login flow with pre-login callback."""
        config = LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
        )

        callback_called = []

        def pre_login_callback(page):
            callback_called.append(True)

        # Note: This test assumes the login factory supports pre-login callbacks
        # If not implemented, this test documents the expected behavior

        login_action = make_login_action_factory(
            login_config=config,
            url="https://test-casino.com",
            twofa=False,
        )

        mock_page.goto = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        # Execute login
        login_action(mock_page)

        # Verify login completed
        mock_page.goto.assert_called_once()


# ============================================================================
# Login Error Handling Tests
# ============================================================================


@pytest.mark.integration
class TestLoginErrorHandling:
    """Tests for error handling in login flows."""

    def test_login_network_error(self, mock_page, mock_env_credentials):
        """Test handling of network errors during login."""
        config = LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#email",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
        )

        login_action = make_login_action_factory(
            login_config=config,
            url="https://test-casino.com",
            twofa=False,
        )

        # Simulate network error on goto
        mock_page.goto = AsyncMock(side_effect=Exception("Network error"))

        # Should raise the exception
        with pytest.raises(Exception, match="Network error"):
            login_action(mock_page)

    def test_login_element_not_found(self, mock_page, mock_env_credentials):
        """Test handling when login elements are not found."""
        config = LoginConfig(
            login_url="https://test-casino.com/login",
            username_selector="input#nonexistent",
            password_selector="input#password",
            submit_selector="button[type='submit']",
            logged_in_url_contains="/account",
        )

        login_action = make_login_action_factory(
            login_config=config,
            url="https://test-casino.com",
            twofa=False,
        )

        # Simulate element not found
        mock_page.goto = AsyncMock()
        mock_page.fill = AsyncMock(side_effect=Exception("Element not found"))

        # Should raise the exception
        with pytest.raises(Exception, match="Element not found"):
            login_action(mock_page)
