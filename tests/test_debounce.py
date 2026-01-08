"""Tests for the debounce utility."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from bluebubbles_linux.utils.debounce import CallDebouncer, Debouncer


class TestDebouncer:
    """Test the Debouncer class for batching items."""

    def test_single_item_fires_after_delay(self) -> None:
        """A single item should fire after the delay."""
        results: list[list[str]] = []
        debouncer: Debouncer[str] = Debouncer(
            callback=lambda items: results.append(items),
            delay_ms=50,
        )

        debouncer.add("item1")
        assert debouncer.pending_count == 1

        # Wait for debounce to fire
        time.sleep(0.1)

        assert len(results) == 1
        assert results[0] == ["item1"]
        assert debouncer.pending_count == 0

    def test_multiple_items_batched_together(self) -> None:
        """Multiple items added rapidly should be batched."""
        results: list[list[str]] = []
        debouncer: Debouncer[str] = Debouncer(
            callback=lambda items: results.append(items),
            delay_ms=100,
        )

        # Add items rapidly
        debouncer.add("item1")
        debouncer.add("item2")
        debouncer.add("item3")

        assert debouncer.pending_count == 3
        assert len(results) == 0  # Not fired yet

        # Wait for debounce
        time.sleep(0.15)

        assert len(results) == 1
        assert results[0] == ["item1", "item2", "item3"]

    def test_add_many(self) -> None:
        """add_many should add multiple items at once."""
        results: list[list[int]] = []
        debouncer: Debouncer[int] = Debouncer(
            callback=lambda items: results.append(items),
            delay_ms=50,
        )

        debouncer.add_many([1, 2, 3])
        assert debouncer.pending_count == 3

        time.sleep(0.1)

        assert results == [[1, 2, 3]]

    def test_add_many_empty_list_is_noop(self) -> None:
        """add_many with empty list should do nothing."""
        callback = MagicMock()
        debouncer: Debouncer[int] = Debouncer(callback=callback, delay_ms=50)

        debouncer.add_many([])
        assert debouncer.pending_count == 0

        time.sleep(0.1)
        callback.assert_not_called()

    def test_timer_resets_on_new_items(self) -> None:
        """Adding items should reset the timer."""
        results: list[list[str]] = []
        debouncer: Debouncer[str] = Debouncer(
            callback=lambda items: results.append(items),
            delay_ms=100,
        )

        debouncer.add("item1")
        time.sleep(0.05)  # Half the delay
        debouncer.add("item2")
        time.sleep(0.05)  # Half the delay again
        debouncer.add("item3")

        # At this point, ~100ms have passed total but timer keeps resetting
        assert len(results) == 0
        assert debouncer.pending_count == 3

        # Now wait for the full delay
        time.sleep(0.15)

        assert len(results) == 1
        assert results[0] == ["item1", "item2", "item3"]

    def test_flush_fires_immediately(self) -> None:
        """flush() should fire pending items immediately."""
        results: list[list[str]] = []
        debouncer: Debouncer[str] = Debouncer(
            callback=lambda items: results.append(items),
            delay_ms=1000,  # Long delay
        )

        debouncer.add("item1")
        debouncer.add("item2")
        debouncer.flush()

        assert len(results) == 1
        assert results[0] == ["item1", "item2"]
        assert debouncer.pending_count == 0

    def test_flush_with_no_pending_is_noop(self) -> None:
        """flush() with no pending items should do nothing."""
        callback = MagicMock()
        debouncer: Debouncer[int] = Debouncer(callback=callback, delay_ms=50)

        debouncer.flush()
        callback.assert_not_called()

    def test_cancel_discards_pending(self) -> None:
        """cancel() should discard pending items without firing."""
        callback = MagicMock()
        debouncer: Debouncer[str] = Debouncer(callback=callback, delay_ms=50)

        debouncer.add("item1")
        debouncer.add("item2")
        debouncer.cancel()

        assert debouncer.pending_count == 0

        # Wait to ensure it doesn't fire
        time.sleep(0.1)
        callback.assert_not_called()

    def test_has_pending_property(self) -> None:
        """has_pending should reflect pending state."""
        debouncer: Debouncer[str] = Debouncer(
            callback=lambda items: None,
            delay_ms=100,
        )

        assert debouncer.has_pending is False

        debouncer.add("item")
        assert debouncer.has_pending is True

        debouncer.cancel()
        assert debouncer.has_pending is False

    def test_thread_safety(self) -> None:
        """Multiple threads adding items should work correctly."""
        results: list[list[int]] = []
        lock = threading.Lock()

        def safe_callback(items: list[int]) -> None:
            with lock:
                results.append(items)

        debouncer: Debouncer[int] = Debouncer(
            callback=safe_callback,
            delay_ms=100,
        )

        # Start multiple threads adding items
        threads = []
        for i in range(5):
            t = threading.Thread(target=lambda x=i: debouncer.add(x))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Wait for debounce
        time.sleep(0.15)

        assert len(results) == 1
        assert len(results[0]) == 5
        assert set(results[0]) == {0, 1, 2, 3, 4}


class TestCallDebouncer:
    """Test the CallDebouncer class for debouncing function calls."""

    def test_single_call_fires_after_delay(self) -> None:
        """A single call should fire after the delay."""
        counter = {"value": 0}
        debouncer = CallDebouncer(
            callback=lambda: counter.__setitem__("value", counter["value"] + 1),
            delay_ms=50,
        )

        debouncer.call()
        assert debouncer.is_pending is True

        time.sleep(0.1)

        assert counter["value"] == 1
        assert debouncer.is_pending is False

    def test_multiple_calls_fire_once(self) -> None:
        """Multiple rapid calls should result in one callback."""
        counter = {"value": 0}
        debouncer = CallDebouncer(
            callback=lambda: counter.__setitem__("value", counter["value"] + 1),
            delay_ms=100,
        )

        debouncer.call()
        debouncer.call()
        debouncer.call()

        assert counter["value"] == 0  # Not fired yet

        time.sleep(0.15)

        assert counter["value"] == 1  # Fired only once

    def test_timer_resets_on_new_calls(self) -> None:
        """New calls should reset the timer."""
        counter = {"value": 0}
        debouncer = CallDebouncer(
            callback=lambda: counter.__setitem__("value", counter["value"] + 1),
            delay_ms=100,
        )

        debouncer.call()
        time.sleep(0.05)
        debouncer.call()
        time.sleep(0.05)
        debouncer.call()

        assert counter["value"] == 0

        time.sleep(0.15)
        assert counter["value"] == 1

    def test_flush_fires_immediately(self) -> None:
        """flush() should fire immediately."""
        counter = {"value": 0}
        debouncer = CallDebouncer(
            callback=lambda: counter.__setitem__("value", counter["value"] + 1),
            delay_ms=1000,
        )

        debouncer.call()
        debouncer.flush()

        assert counter["value"] == 1
        assert debouncer.is_pending is False

    def test_flush_with_no_pending_is_noop(self) -> None:
        """flush() with no pending call should do nothing."""
        callback = MagicMock()
        debouncer = CallDebouncer(callback=callback, delay_ms=50)

        debouncer.flush()
        callback.assert_not_called()

    def test_cancel_prevents_callback(self) -> None:
        """cancel() should prevent the callback from firing."""
        callback = MagicMock()
        debouncer = CallDebouncer(callback=callback, delay_ms=50)

        debouncer.call()
        debouncer.cancel()

        assert debouncer.is_pending is False

        time.sleep(0.1)
        callback.assert_not_called()

    def test_separate_call_sequences(self) -> None:
        """Calls after debounce fires should start a new sequence."""
        counter = {"value": 0}
        debouncer = CallDebouncer(
            callback=lambda: counter.__setitem__("value", counter["value"] + 1),
            delay_ms=50,
        )

        # First sequence
        debouncer.call()
        time.sleep(0.1)
        assert counter["value"] == 1

        # Second sequence
        debouncer.call()
        time.sleep(0.1)
        assert counter["value"] == 2
