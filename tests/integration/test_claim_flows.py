"""Integration tests for claim flows using mocked Playwright objects."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from casino import (
    Currency,
    CurrencyDisplayConfig,
    GenericClaimConfig,
    MTBClaimConfig,
    make_generic_accept_or_close_modals,
    make_modal_tab_button,
)


# ============================================================================
# MTB (Modal-Tab-Button) Claim Pattern Tests
# ============================================================================


@pytest.mark.integration
class TestMTBClaimPattern:
    """Integration tests for Modal-Tab-Button claim pattern."""

    @pytest.fixture
    def mtb_config(self):
        """Create a basic MTB claim configuration."""
        return MTBClaimConfig(
            modal_toggle_selector="button.claim-modal-toggle",
            tab_selector="button[data-tab='daily']",
            claim_button_selector="button.claim-now",
            close_selector="button.modal-close",
        )

    def test_make_modal_tab_button_basic_flow(self, mtb_config, mock_page):
        """Test basic MTB claim flow."""
        claim_action = make_modal_tab_button(mtb_config)

        # Mock page methods
        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        # Execute claim
        claim_action(mock_page)

        # Verify the sequence: toggle -> tab -> claim -> close
        click_calls = [call[0][0] for call in mock_page.click.call_args_list]

        # Should have at least 4 clicks: toggle, tab, claim, close
        assert len(click_calls) >= 4
        assert mtb_config.modal_toggle_selector in click_calls
        assert mtb_config.tab_selector in click_calls
        assert mtb_config.claim_button_selector in click_calls
        assert mtb_config.close_selector in click_calls

    def test_mtb_with_delays(self, mtb_config, mock_page):
        """Test that MTB claim includes delays between actions."""
        claim_action = make_modal_tab_button(mtb_config)

        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        claim_action(mock_page)

        # Verify delays were added between clicks
        assert mock_page.wait_for_timeout.call_count >= 3

    def test_mtb_click_failure_handling(self, mtb_config, mock_page):
        """Test MTB claim when a click fails."""
        claim_action = make_modal_tab_button(mtb_config)

        # First click succeeds, second fails
        mock_page.click = AsyncMock(side_effect=[None, Exception("Click failed"), None, None])
        mock_page.wait_for_timeout = AsyncMock()

        # Should raise the exception
        with pytest.raises(Exception, match="Click failed"):
            claim_action(mock_page)


# ============================================================================
# Generic Accept/Close Modals Pattern Tests
# ============================================================================


@pytest.mark.integration
class TestGenericClaimPattern:
    """Integration tests for Generic Accept/Close claim pattern."""

    @pytest.fixture
    def generic_config(self):
        """Create a basic generic claim configuration."""
        return GenericClaimConfig(
            accept_selectors=["button.accept", "button.claim", "button.ok"],
            close_selectors=["button.close", "button.dismiss"],
            max_iterations=5,
        )

    def test_make_generic_accept_or_close_modals(self, generic_config, mock_page):
        """Test generic modal handling."""
        claim_action = make_generic_accept_or_close_modals(generic_config)

        # Mock page to have no visible modals
        mock_page.query_selector = Mock(return_value=None)
        mock_page.wait_for_timeout = AsyncMock()

        # Execute claim
        claim_action(mock_page)

        # Should check for modal selectors
        assert mock_page.query_selector.call_count > 0

    def test_generic_accepts_modal(self, generic_config, mock_page):
        """Test that generic pattern accepts visible modals."""
        claim_action = make_generic_accept_or_close_modals(generic_config)

        # Mock a visible accept button
        mock_accept_element = Mock()
        mock_accept_element.click = AsyncMock()

        # First call finds accept button, subsequent calls find nothing
        mock_page.query_selector = Mock(side_effect=[mock_accept_element, None, None])
        mock_page.wait_for_timeout = AsyncMock()

        claim_action(mock_page)

        # Verify accept button was clicked
        mock_accept_element.click.assert_called_once()

    def test_generic_closes_modal(self, generic_config, mock_page):
        """Test that generic pattern closes modals when no accept button."""
        claim_action = make_generic_accept_or_close_modals(generic_config)

        # Mock a visible close button (no accept found)
        mock_close_element = Mock()
        mock_close_element.click = AsyncMock()

        # Accept checks return None, close check finds element
        accept_count = len(generic_config.accept_selectors)
        mock_page.query_selector = Mock(
            side_effect=[None] * accept_count + [mock_close_element, None]
        )
        mock_page.wait_for_timeout = AsyncMock()

        claim_action(mock_page)

        # Verify close button was clicked
        mock_close_element.click.assert_called_once()

    def test_generic_max_iterations(self, generic_config, mock_page):
        """Test that generic pattern respects max iterations."""
        claim_action = make_generic_accept_or_close_modals(generic_config)

        # Mock always finding modals (infinite loop scenario)
        mock_accept_element = Mock()
        mock_accept_element.click = AsyncMock()
        mock_page.query_selector = Mock(return_value=mock_accept_element)
        mock_page.wait_for_timeout = AsyncMock()

        claim_action(mock_page)

        # Should stop after max_iterations
        # Each iteration tries all accept selectors
        max_clicks = generic_config.max_iterations
        assert mock_accept_element.click.call_count <= max_clicks


# ============================================================================
# Currency Display Configuration Tests
# ============================================================================


@pytest.mark.integration
class TestCurrencyDisplayIntegration:
    """Integration tests for currency display configuration."""

    @pytest.fixture
    def multi_currency_config(self):
        """Create a multi-currency display configuration."""
        sc = Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=["span.sc-balance", "div.sweeps-balance"],
        )
        gc = Currency(
            name="Gold Coins",
            code="GC",
            selectors=["span.gc-balance", "div.gold-balance"],
        )

        return CurrencyDisplayConfig(
            currencies=[sc, gc],
            currency_toggle_dropdown_selector="button.currency-dropdown",
        )

    def test_currency_display_with_toggle(self, multi_currency_config, mock_page):
        """Test currency display with dropdown toggle."""
        from casino import make_get_casino_account_state

        get_state = make_get_casino_account_state(multi_currency_config)

        # Mock page elements
        mock_page.locator = Mock()
        mock_locator = Mock()
        mock_locator.inner_text = Mock(return_value="100.50")
        mock_locator.is_visible = Mock(return_value=True)
        mock_page.locator.return_value = mock_locator

        # Execute state retrieval
        state = get_state(mock_page)

        # Verify state was retrieved
        assert state is not None

    def test_currency_balance_parsing(self, mock_page):
        """Test parsing currency balances from page."""
        sc = Currency(
            name="Sweeps Coins",
            code="SC",
            selectors=["span.sc-balance"],
        )

        config = CurrencyDisplayConfig(currencies=[sc])

        from casino import make_get_casino_account_state

        get_state = make_get_casino_account_state(config)

        # Mock balance element
        mock_locator = Mock()
        mock_locator.inner_text = Mock(return_value="$123.45")
        mock_locator.is_visible = Mock(return_value=True)
        mock_page.locator = Mock(return_value=mock_locator)

        state = get_state(mock_page)

        assert state is not None


# ============================================================================
# Account State Retrieval Tests
# ============================================================================


@pytest.mark.integration
class TestAccountStateRetrieval:
    """Integration tests for casino account state retrieval."""

    def test_get_casino_account_state_basic(self, mock_page):
        """Test basic account state retrieval."""
        from casino import CurrencyDisplayConfig, Currency, make_get_casino_account_state

        sc = Currency(name="SC", code="SC", selectors=["span.sc"])
        config = CurrencyDisplayConfig(currencies=[sc])

        get_state = make_get_casino_account_state(config)

        # Mock balance element
        mock_locator = Mock()
        mock_locator.inner_text = Mock(return_value="100.50")
        mock_locator.is_visible = Mock(return_value=True)
        mock_page.locator = Mock(return_value=mock_locator)

        state = get_state(mock_page)

        # Verify state object was created
        assert state is not None
        assert hasattr(state, "sweeps_coins")
        assert hasattr(state, "gold_coins")

    def test_get_casino_account_state_multiple_currencies(self, mock_page):
        """Test account state retrieval with multiple currencies."""
        from casino import CurrencyDisplayConfig, Currency, make_get_casino_account_state

        sc = Currency(name="SC", code="SC", selectors=["span.sc"])
        gc = Currency(name="GC", code="GC", selectors=["span.gc"])
        config = CurrencyDisplayConfig(currencies=[sc, gc])

        get_state = make_get_casino_account_state(config)

        # Mock different balances for each currency
        def mock_locator_factory(selector):
            locator = Mock()
            if "sc" in selector:
                locator.inner_text = Mock(return_value="100.50")
            else:
                locator.inner_text = Mock(return_value="2500.00")
            locator.is_visible = Mock(return_value=True)
            return locator

        mock_page.locator = Mock(side_effect=mock_locator_factory)

        state = get_state(mock_page)

        assert state is not None


# ============================================================================
# Claim Flow Error Handling
# ============================================================================


@pytest.mark.integration
class TestClaimFlowErrorHandling:
    """Tests for error handling in claim flows."""

    def test_mtb_modal_not_found(self, mock_page):
        """Test MTB claim when modal toggle is not found."""
        config = MTBClaimConfig(
            modal_toggle_selector="button.nonexistent",
            tab_selector="button.tab",
            claim_button_selector="button.claim",
            close_selector="button.close",
        )

        claim_action = make_modal_tab_button(config)

        # Simulate element not found
        mock_page.click = AsyncMock(side_effect=Exception("Element not found"))

        with pytest.raises(Exception, match="Element not found"):
            claim_action(mock_page)

    def test_generic_all_selectors_fail(self, mock_page):
        """Test generic claim when all selectors fail to find elements."""
        config = GenericClaimConfig(
            accept_selectors=["button.accept"],
            close_selectors=["button.close"],
            max_iterations=3,
        )

        claim_action = make_generic_accept_or_close_modals(config)

        # No elements found
        mock_page.query_selector = Mock(return_value=None)
        mock_page.wait_for_timeout = AsyncMock()

        # Should complete without errors (no modals to handle)
        claim_action(mock_page)

        # Verify it tried to find elements
        assert mock_page.query_selector.call_count > 0


# ============================================================================
# End-to-End Claim Flow Integration
# ============================================================================


@pytest.mark.integration
class TestClaimFlowEndToEnd:
    """End-to-end integration tests for complete claim flows."""

    def test_complete_mtb_claim_flow(self, mock_page):
        """Test complete MTB claim flow from start to finish."""
        config = MTBClaimConfig(
            modal_toggle_selector="button#open-bonus",
            tab_selector="button[data-tab='daily']",
            claim_button_selector="button.claim-bonus",
            close_selector="button.close-modal",
        )

        claim_action = make_modal_tab_button(config)

        # Mock successful flow
        mock_page.click = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()

        claim_action(mock_page)

        # Verify complete flow executed
        assert mock_page.click.call_count >= 4
        assert mock_page.wait_for_timeout.call_count >= 3

    def test_complete_generic_claim_flow(self, mock_page):
        """Test complete generic claim flow."""
        config = GenericClaimConfig(
            accept_selectors=["button.accept-bonus", "button.claim-daily"],
            close_selectors=["button.close", "button.dismiss"],
            max_iterations=10,
        )

        claim_action = make_generic_accept_or_close_modals(config)

        # Simulate finding and accepting one modal
        mock_accept = Mock()
        mock_accept.click = AsyncMock()
        mock_page.query_selector = Mock(side_effect=[mock_accept] + [None] * 20)
        mock_page.wait_for_timeout = AsyncMock()

        claim_action(mock_page)

        # Verify modal was accepted
        mock_accept.click.assert_called_once()
