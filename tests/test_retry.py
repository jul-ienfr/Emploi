from __future__ import annotations

from unittest.mock import patch

import pytest

from emploi.retry import with_retry


class TestWithRetry:
    def test_success_on_first_attempt(self):
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert succeed() == "ok"
        assert call_count == 1

    def test_retry_then_success(self):
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01)
        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "ok"

        assert fail_twice() == "ok"
        assert call_count == 3

    def test_exhaustion_raises_original(self):
        @with_retry(max_retries=2, base_delay=0.01)
        def always_fail():
            raise ConnectionError("permanent")

        with pytest.raises(ConnectionError, match="permanent"):
            always_fail()

    def test_non_retryable_passes_through(self):
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01, retryable_exceptions=(ConnectionError,))
        def value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            value_error()
        assert call_count == 1

    @patch("emploi.retry.time.sleep")
    def test_backoff_timing(self, mock_sleep):
        call_count = 0

        @with_retry(max_retries=3, base_delay=1.0, max_delay=30.0)
        def fail_thrice():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise OSError("transient")
            return "ok"

        result = fail_thrice()
        assert result == "ok"
        assert call_count == 4
        # 3 retries with exponential backoff: 1.0, 2.0, 4.0 (plus jitter)
        assert mock_sleep.call_count == 3
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays[0] >= 1.0
        assert delays[1] >= 2.0
        assert delays[2] >= 4.0

    @patch("emploi.retry.time.sleep")
    def test_max_delay_cap(self, mock_sleep):
        call_count = 0

        @with_retry(max_retries=5, base_delay=10.0, max_delay=15.0)
        def fail():
            nonlocal call_count
            call_count += 1
            if call_count < 6:
                raise OSError("transient")
            return "ok"

        result = fail()
        assert result == "ok"
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert all(d <= 15.5 for d in delays)  # 15.0 + jitter cap

    def test_preserves_function_metadata(self):
        @with_retry(max_retries=1, base_delay=0.01)
        def documented():
            """My docstring."""
            return True

        assert documented.__doc__ == "My docstring."
        assert documented.__name__ == "documented"
