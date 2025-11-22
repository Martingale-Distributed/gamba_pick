"""Unit tests for scrapling_pick.py utility functions."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from scrapling_pick import (
    Pick,
    filter_picks,
    json_to_pick,
    load_picks,
    pick_to_dict_json_safe,
    save_picks,
)


# ============================================================================
# Pick Serialization Tests
# ============================================================================


@pytest.mark.unit
class TestPickSerialization:
    """Tests for Pick serialization and deserialization."""

    def test_pick_to_dict_json_safe_basic(self):
        """Test basic Pick to dict conversion."""
        pick = Pick(url="https://tronpick.io", currency="TRX")
        pick.balance = 100.50
        pick.wagered = 50.25
        pick.target = 150.00
        pick.remaining_claims = 3
        pick.free_spins = 5
        pick.last_update = datetime(2025, 1, 1, 12, 0, 0)

        result = pick_to_dict_json_safe(pick)

        assert result["url"] == "https://tronpick.io"
        assert result["currency"] == "TRX"
        assert result["balance"] == 100.50
        assert result["wagered"] == 50.25
        assert result["target"] == 150.00
        assert result["remaining_claims"] == 3
        assert result["free_spins"] == 5
        assert result["last_update"] == "2025-01-01T12:00:00"

    def test_pick_to_dict_excludes_cooldown_timer(self):
        """Test that cooldown_timer is not included in serialization."""
        pick = Pick(url="https://tronpick.io", currency="TRX")
        pick.cooldown_timer = "05:30"

        result = pick_to_dict_json_safe(pick)

        assert "cooldown_timer" not in result

    def test_pick_to_dict_with_history(self):
        """Test serialization with history."""
        pick = Pick(url="https://tronpick.io", currency="TRX")
        dt1 = datetime(2025, 1, 1, 12, 0, 0)
        dt2 = datetime(2025, 1, 2, 12, 0, 0)
        pick.history = [
            (dt1, 100.0, 50.0, 150.0, 3, 5),
            (dt2, 110.0, 60.0, 150.0, 2, 4),
        ]

        result = pick_to_dict_json_safe(pick)

        assert len(result["history"]) == 2
        assert result["history"][0] == ("2025-01-01T12:00:00", 100.0, 50.0, 150.0, 3, 5)
        assert result["history"][1] == ("2025-01-02T12:00:00", 110.0, 60.0, 150.0, 2, 4)

    def test_pick_to_dict_none_last_update(self):
        """Test serialization when last_update is None."""
        pick = Pick(url="https://tronpick.io", currency="TRX")
        pick.last_update = None

        result = pick_to_dict_json_safe(pick)

        assert result["last_update"] is None

    def test_json_to_pick_basic(self):
        """Test basic dict to Pick conversion."""
        data = {
            "url": "https://tronpick.io",
            "currency": "TRX",
            "balance": 100.50,
            "wagered": 50.25,
            "target": 150.00,
            "remaining_claims": 3,
            "free_spins": 5,
            "last_update": "2025-01-01T12:00:00",
            "history": [],
        }

        pick = json_to_pick(data)

        assert pick.url == "https://tronpick.io"
        assert pick.currency == "TRX"
        assert pick.balance == 100.50
        assert pick.wagered == 50.25
        assert pick.target == 150.00
        assert pick.remaining_claims == 3
        assert pick.free_spins == 5
        assert pick.last_update == datetime(2025, 1, 1, 12, 0, 0)

    def test_json_to_pick_with_history(self):
        """Test deserialization with history."""
        data = {
            "url": "https://tronpick.io",
            "currency": "TRX",
            "balance": 110.0,
            "wagered": 60.0,
            "target": 150.0,
            "remaining_claims": 2,
            "free_spins": 4,
            "last_update": "2025-01-02T12:00:00",
            "history": [
                ("2025-01-01T12:00:00", 100.0, 50.0, 150.0, 3, 5),
                ("2025-01-02T12:00:00", 110.0, 60.0, 150.0, 2, 4),
            ],
        }

        pick = json_to_pick(data)

        assert len(pick.history) == 2
        assert pick.history[0] == (datetime(2025, 1, 1, 12, 0, 0), 100.0, 50.0, 150.0, 3, 5)
        assert pick.history[1] == (datetime(2025, 1, 2, 12, 0, 0), 110.0, 60.0, 150.0, 2, 4)

    def test_json_to_pick_missing_optional_fields(self):
        """Test deserialization with missing optional fields."""
        data = {
            "url": "https://tronpick.io",
            "currency": "TRX",
            "balance": 100.0,
            "wagered": 50.0,
            "target": 150.0,
            "last_update": None,
            "history": [],
        }

        pick = json_to_pick(data)

        assert pick.remaining_claims == 0  # Default value
        assert pick.free_spins == 0  # Default value
        assert pick.last_update is None

    def test_roundtrip_serialization(self):
        """Test that serialization and deserialization are inverses."""
        original = Pick(url="https://tronpick.io", currency="TRX")
        original.balance = 100.50
        original.wagered = 50.25
        original.target = 150.00
        original.remaining_claims = 3
        original.free_spins = 5
        original.last_update = datetime(2025, 1, 1, 12, 0, 0)
        original.history = [
            (datetime(2025, 1, 1, 12, 0, 0), 100.0, 50.0, 150.0, 3, 5),
        ]

        # Serialize then deserialize
        data = pick_to_dict_json_safe(original)
        restored = json_to_pick(data)

        # Compare all fields (except cooldown_timer which is ephemeral)
        assert restored.url == original.url
        assert restored.currency == original.currency
        assert restored.balance == original.balance
        assert restored.wagered == original.wagered
        assert restored.target == original.target
        assert restored.remaining_claims == original.remaining_claims
        assert restored.free_spins == original.free_spins
        assert restored.last_update == original.last_update
        assert len(restored.history) == len(original.history)
        assert restored.history[0] == original.history[0]


# ============================================================================
# Pick Save/Load Tests
# ============================================================================


@pytest.mark.unit
class TestPickSaveLoad:
    """Tests for saving and loading picks from files."""

    def test_save_picks_creates_directory(self, temp_dir):
        """Test that save_picks creates directory if it doesn't exist."""
        picks_dir = temp_dir / "test_picks"
        assert not picks_dir.exists()

        pick = Pick(url="https://tronpick.io", currency="TRX")
        save_picks([pick], directory=str(picks_dir))

        assert picks_dir.exists()
        assert picks_dir.is_dir()

    def test_save_picks_creates_json_files(self, temp_dir, monkeypatch):
        """Test that save_picks creates JSON files for each pick."""
        picks_dir = temp_dir / "test_picks"

        # Mock the write method on Pick
        write_calls = []

        def mock_write(self, filepath):
            write_calls.append(filepath)
            # Actually write a valid JSON file
            with open(filepath, "w") as f:
                json.dump(pick_to_dict_json_safe(self), f)

        monkeypatch.setattr(Pick, "write", mock_write)

        pick1 = Pick(url="https://tronpick.io", currency="TRX")
        pick2 = Pick(url="https://ethpick.io", currency="ETH")

        save_picks([pick1, pick2], directory=str(picks_dir))

        assert len(write_calls) == 2

    def test_load_picks_empty_directory(self, temp_dir):
        """Test loading from empty directory."""
        picks_dir = temp_dir / "empty_picks"
        picks_dir.mkdir()

        picks = load_picks(directory=str(picks_dir))

        assert picks == []

    def test_load_picks_nonexistent_directory(self, temp_dir):
        """Test loading from non-existent directory."""
        picks_dir = temp_dir / "nonexistent"

        picks = load_picks(directory=str(picks_dir))

        assert picks == []

    def test_load_picks_with_valid_files(self, temp_dir, monkeypatch):
        """Test loading valid pick files."""
        picks_dir = temp_dir / "test_picks"
        picks_dir.mkdir()

        # Create test pick files
        pick1_data = {
            "url": "https://tronpick.io",
            "currency": "TRX",
            "balance": 100.0,
            "wagered": 50.0,
            "target": 150.0,
            "remaining_claims": 3,
            "free_spins": 5,
            "last_update": "2025-01-01T12:00:00",
            "history": [],
        }

        pick2_data = {
            "url": "https://ethpick.io",
            "currency": "ETH",
            "balance": 200.0,
            "wagered": 100.0,
            "target": 300.0,
            "remaining_claims": 2,
            "free_spins": 3,
            "last_update": "2025-01-02T12:00:00",
            "history": [],
        }

        with open(picks_dir / "pick1.json", "w") as f:
            json.dump(pick1_data, f)

        with open(picks_dir / "pick2.json", "w") as f:
            json.dump(pick2_data, f)

        # Mock Pick.read to use json_to_pick
        def mock_read(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
            return json_to_pick(data)

        monkeypatch.setattr(Pick, "read", staticmethod(mock_read))

        picks = load_picks(directory=str(picks_dir))

        assert len(picks) == 2
        assert any(p.url == "https://tronpick.io" for p in picks)
        assert any(p.url == "https://ethpick.io" for p in picks)

    def test_load_picks_skips_invalid_files(self, temp_dir, monkeypatch):
        """Test that load_picks skips invalid JSON files."""
        picks_dir = temp_dir / "test_picks"
        picks_dir.mkdir()

        # Create one valid and one invalid file
        valid_data = {
            "url": "https://tronpick.io",
            "currency": "TRX",
            "balance": 100.0,
            "wagered": 50.0,
            "target": 150.0,
            "remaining_claims": 3,
            "free_spins": 5,
            "last_update": "2025-01-01T12:00:00",
            "history": [],
        }

        with open(picks_dir / "valid.json", "w") as f:
            json.dump(valid_data, f)

        with open(picks_dir / "invalid.json", "w") as f:
            f.write("not valid json{")

        # Mock Pick.read
        def mock_read(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
            return json_to_pick(data)

        monkeypatch.setattr(Pick, "read", staticmethod(mock_read))

        picks = load_picks(directory=str(picks_dir))

        # Should only load the valid file
        assert len(picks) == 1
        assert picks[0].url == "https://tronpick.io"

    def test_load_picks_ignores_non_json_files(self, temp_dir, monkeypatch):
        """Test that load_picks ignores non-JSON files."""
        picks_dir = temp_dir / "test_picks"
        picks_dir.mkdir()

        # Create JSON and non-JSON files
        valid_data = {
            "url": "https://tronpick.io",
            "currency": "TRX",
            "balance": 100.0,
            "wagered": 50.0,
            "target": 150.0,
            "remaining_claims": 3,
            "free_spins": 5,
            "last_update": "2025-01-01T12:00:00",
            "history": [],
        }

        with open(picks_dir / "data.json", "w") as f:
            json.dump(valid_data, f)

        with open(picks_dir / "readme.txt", "w") as f:
            f.write("This is a text file")

        # Mock Pick.read
        def mock_read(filepath):
            with open(filepath, "r") as f:
                data = json.load(f)
            return json_to_pick(data)

        monkeypatch.setattr(Pick, "read", staticmethod(mock_read))

        picks = load_picks(directory=str(picks_dir))

        # Should only load the JSON file
        assert len(picks) == 1
        assert picks[0].url == "https://tronpick.io"


# ============================================================================
# Filter Picks Tests
# ============================================================================


@pytest.mark.unit
class TestFilterPicks:
    """Tests for filter_picks function."""

    def create_test_picks(self) -> List[Pick]:
        """Create a list of test picks."""
        return [
            Pick(url="https://tronpick.io", currency="TRX"),
            Pick(url="https://ethpick.io", currency="ETH"),
            Pick(url="https://btcpick.io", currency="BTC"),
            Pick(url="https://ltcpick.io", currency="LTC"),
        ]

    def test_filter_picks_no_filters(self):
        """Test with no filters - should return all picks."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=[], skip=[])

        assert len(result) == 4
        assert result == picks

    def test_filter_picks_only_single(self):
        """Test filtering with only parameter - single currency."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=["TRX"], skip=[])

        assert len(result) == 1
        assert result[0].currency == "TRX"

    def test_filter_picks_only_multiple(self):
        """Test filtering with only parameter - multiple currencies."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=["TRX", "ETH"], skip=[])

        assert len(result) == 2
        currencies = [p.currency for p in result]
        assert "TRX" in currencies
        assert "ETH" in currencies
        assert "BTC" not in currencies

    def test_filter_picks_skip_single(self):
        """Test filtering with skip parameter - single currency."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=[], skip=["BTC"])

        assert len(result) == 3
        currencies = [p.currency for p in result]
        assert "BTC" not in currencies
        assert "TRX" in currencies
        assert "ETH" in currencies
        assert "LTC" in currencies

    def test_filter_picks_skip_multiple(self):
        """Test filtering with skip parameter - multiple currencies."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=[], skip=["BTC", "LTC"])

        assert len(result) == 2
        currencies = [p.currency for p in result]
        assert "TRX" in currencies
        assert "ETH" in currencies
        assert "BTC" not in currencies
        assert "LTC" not in currencies

    def test_filter_picks_only_takes_precedence(self):
        """Test that only parameter takes precedence over skip."""
        picks = self.create_test_picks()

        # If both are provided, only should be used
        result = filter_picks(picks, only=["TRX"], skip=["ETH"])

        assert len(result) == 1
        assert result[0].currency == "TRX"

    def test_filter_picks_only_nonexistent_currency(self):
        """Test filtering with non-existent currency in only."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=["XRP"], skip=[])

        assert len(result) == 0

    def test_filter_picks_skip_nonexistent_currency(self):
        """Test filtering with non-existent currency in skip."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=[], skip=["XRP"])

        assert len(result) == 4  # All picks should remain

    def test_filter_picks_empty_input(self):
        """Test filtering with empty input list."""
        result = filter_picks([], only=[], skip=[])

        assert result == []

    def test_filter_picks_skip_all(self):
        """Test filtering that excludes all picks."""
        picks = self.create_test_picks()

        result = filter_picks(picks, only=[], skip=["TRX", "ETH", "BTC", "LTC"])

        assert len(result) == 0

    def test_filter_picks_case_sensitivity(self):
        """Test that filtering is case-sensitive."""
        picks = self.create_test_picks()

        # Using lowercase should not match
        result = filter_picks(picks, only=["trx"], skip=[])

        assert len(result) == 0
