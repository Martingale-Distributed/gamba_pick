# gamba_pick Test Suite

Comprehensive test suite for the gamba_pick casino automation and faucet claiming tool.

## Overview

This test suite provides extensive coverage for:
- **Unit tests**: Utility functions, data classes, and serialization
- **Integration tests**: Login flows, claim flows, and browser automation
- **E2E tests**: Full end-to-end scenarios (requires credentials)

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and test configuration
├── unit/                    # Unit tests (no browser required)
│   ├── test_casino_utils.py
│   ├── test_scrapling_pick_utils.py
│   └── test_data_classes.py
├── integration/             # Integration tests (mocked browser)
│   ├── test_login_flows.py
│   └── test_claim_flows.py
├── e2e/                     # End-to-end tests (requires credentials)
└── fixtures/                # Test data and mock responses
    ├── mock_pages/
    ├── mock_responses/
    └── test_configs/
```

## Installation

### Install Test Dependencies

```bash
# Using uv (recommended)
uv pip install -e ".[test]"

# Using pip
pip install -e ".[test]"
```

This installs:
- pytest and plugins (pytest-playwright, pytest-asyncio, pytest-cov, etc.)
- Property-based testing (hypothesis)
- Time mocking (freezegun)
- HTTP mocking (responses)

### Install Playwright Browsers (for integration tests)

```bash
playwright install chromium
```

## Running Tests

### Run All Tests

```bash
pytest
```

### Run Specific Test Categories

```bash
# Unit tests only (fast, no browser)
pytest -m unit

# Integration tests only (mocked browser)
pytest -m integration

# End-to-end tests only (requires credentials)
pytest -m e2e

# Exclude slow tests
pytest -m "not slow"
```

### Run Specific Test Files

```bash
# Run casino utility tests
pytest tests/unit/test_casino_utils.py

# Run login flow tests
pytest tests/integration/test_login_flows.py

# Run a specific test
pytest tests/unit/test_casino_utils.py::TestUrlToEnvPrefix::test_basic_url
```

### Run with Coverage

```bash
# Generate coverage report
pytest --cov=. --cov-report=html

# View coverage report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Run in Parallel (faster)

```bash
# Run tests in parallel using 4 workers
pytest -n 4
```

## Test Markers

Tests are organized with markers for selective execution:

- `@pytest.mark.unit` - Fast unit tests, no external dependencies
- `@pytest.mark.integration` - Integration tests with mocked browser
- `@pytest.mark.e2e` - End-to-end tests requiring real browser and credentials
- `@pytest.mark.slow` - Tests that take a long time to run
- `@pytest.mark.requires_credentials` - Tests that need casino/faucet credentials

## Environment Variables for E2E Tests

End-to-end tests require casino credentials:

```bash
# Stake.us
export STAKE_US_USERNAME="your-email@example.com"
export STAKE_US_PASSWORD="your-password"
export STAKE_US_2FA="your-totp-secret"  # Optional, for 2FA

# LuckyBird.io
export LUCKYBIRD_IO_USERNAME="your-email@example.com"
export LUCKYBIRD_IO_PASSWORD="your-password"
```

**⚠️ Never commit credentials to version control!**

## Test Coverage

### Unit Tests

**casino.py utilities:**
- `url_to_env_prefix()` - URL to environment variable conversion
- `get_credentials()` - Credential retrieval from environment
- `gaussian_random_delay()` - Human-like delay generation
- `wait_for_clickable()` - Element clickability waiting
- `safe_click()` - Click with retry logic

**scrapling_pick.py utilities:**
- `pick_to_dict_json_safe()` - Pick serialization
- `json_to_pick()` - Pick deserialization
- `save_picks()` / `load_picks()` - File I/O
- `filter_picks()` - Pick filtering logic

**Data classes:**
- `CasinoAccountState` - Casino account representation
- `Currency` / `CurrencyDisplayConfig` - Currency configuration
- `LoginConfig` - Login form configuration
- `AccountState` - Faucet account state
- `Pick` - Faucet pick history

### Integration Tests

**Login flows:**
- Basic username/password login
- TOTP/2FA authentication
- Google One Tap popup handling
- Pre-login callbacks
- Error handling (network errors, missing elements)

**Claim flows:**
- MTB (Modal-Tab-Button) claim pattern
- Generic accept/close modal pattern
- Multi-currency balance retrieval
- Account state parsing
- Error handling

### E2E Tests (Coming Soon)

- Full Stake.us automation
- Full LuckyBird.io automation
- Multi-casino workflows

## Writing New Tests

### Unit Test Template

```python
import pytest
from mymodule import my_function

@pytest.mark.unit
class TestMyFunction:
    """Tests for my_function."""

    def test_basic_case(self):
        """Test basic functionality."""
        result = my_function("input")
        assert result == "expected"

    def test_edge_case(self):
        """Test edge case."""
        result = my_function("")
        assert result == ""
```

### Integration Test Template

```python
import pytest
from unittest.mock import AsyncMock

@pytest.mark.integration
class TestMyIntegration:
    """Integration tests for my feature."""

    def test_with_mocked_page(self, mock_page):
        """Test with mocked Playwright page."""
        mock_page.click = AsyncMock()
        # Your test code here
        assert mock_page.click.called
```

## Continuous Integration

Tests run automatically on:
- Pull requests
- Commits to main branch
- Scheduled nightly builds

CI configuration runs:
1. Unit tests (always)
2. Integration tests (always)
3. E2E tests (only if credentials available)
4. Coverage report generation
5. Test result reporting

## Debugging Tests

### Run with Verbose Output

```bash
pytest -v
```

### Show Print Statements

```bash
pytest -s
```

### Drop into Debugger on Failure

```bash
pytest --pdb
```

### Run Last Failed Tests

```bash
pytest --lf
```

### Run Tests Modified Since Last Commit

```bash
pytest --testmon
```

## Common Issues

### Import Errors

If you see import errors, ensure you've installed the package in editable mode:

```bash
pip install -e .
```

### Playwright Errors

If integration tests fail with Playwright errors:

```bash
# Reinstall browsers
playwright install chromium

# Or install all browsers
playwright install
```

### Fixture Not Found

Ensure you're running pytest from the project root directory where `conftest.py` is located.

## Performance

Typical test execution times:
- **Unit tests**: ~2-5 seconds (150+ tests)
- **Integration tests**: ~10-20 seconds (50+ tests)
- **E2E tests**: ~60-120 seconds per casino

Use `pytest -n auto` to run tests in parallel and reduce execution time.

## Contributing

When adding new features:

1. Write unit tests for utility functions
2. Write integration tests for workflows
3. Add E2E tests for critical paths (optional)
4. Ensure all tests pass: `pytest`
5. Check coverage: `pytest --cov`
6. Aim for >80% coverage on new code

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-playwright](https://playwright.dev/python/docs/test-runners)
- [hypothesis property testing](https://hypothesis.readthedocs.io/)
- [Coverage.py](https://coverage.readthedocs.io/)
