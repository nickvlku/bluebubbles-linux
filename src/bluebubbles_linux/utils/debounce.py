"""Debounce utility for batching rapid UI updates."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Debouncer(Generic[T]):
    """
    Debounces rapid calls to a function, batching items together.

    When items are added via `add()`, they are collected into a batch.
    After `delay_ms` milliseconds of no new items, the `callback` is
    invoked with all collected items.

    This is useful for batching rapid UI updates (e.g., multiple incoming
    messages) into a single update operation.

    Thread-safe: can be called from any thread, callback is invoked on
    the thread that created the debouncer (typically main thread when
    used with GLib.idle_add).
    """

    def __init__(
        self,
        callback: Callable[[list[T]], None],
        delay_ms: int = 100,
        scheduler: Callable[[Callable[[], bool]], int] | None = None,
        cancel_scheduler: Callable[[int], None] | None = None,
    ) -> None:
        """
        Initialize the debouncer.

        Args:
            callback: Function to call with batched items when debounce fires.
            delay_ms: Milliseconds to wait after last item before firing.
            scheduler: Function to schedule callback (e.g., GLib.timeout_add).
                       Should return an ID that can be used to cancel.
                       If None, uses threading.Timer (for testing).
            cancel_scheduler: Function to cancel scheduled callback (e.g., GLib.source_remove).
                              If None, uses Timer.cancel() (for testing).
        """
        self._callback = callback
        self._delay_ms = delay_ms
        self._scheduler = scheduler
        self._cancel_scheduler = cancel_scheduler

        self._lock = threading.Lock()
        self._pending_items: list[T] = []
        self._timer_id: int | threading.Timer | None = None

    def add(self, item: T) -> None:
        """Add an item to the pending batch and reset the timer."""
        with self._lock:
            self._pending_items.append(item)
            self._reset_timer()

    def add_many(self, items: list[T]) -> None:
        """Add multiple items to the pending batch and reset the timer."""
        if not items:
            return
        with self._lock:
            self._pending_items.extend(items)
            self._reset_timer()

    def _reset_timer(self) -> None:
        """Cancel existing timer and start a new one. Must be called with lock held."""
        # Cancel existing timer
        if self._timer_id is not None:
            if self._cancel_scheduler:
                self._cancel_scheduler(self._timer_id)  # type: ignore
            elif isinstance(self._timer_id, threading.Timer):
                self._timer_id.cancel()
            self._timer_id = None

        # Schedule new timer
        if self._scheduler:
            self._timer_id = self._scheduler(self._on_timer)
        else:
            # Fallback to threading.Timer for testing
            timer = threading.Timer(self._delay_ms / 1000.0, self._on_timer_thread)
            timer.daemon = True
            timer.start()
            self._timer_id = timer

    def _on_timer(self) -> bool:
        """Timer callback for GLib scheduler. Returns False to not repeat."""
        self._fire()
        return False

    def _on_timer_thread(self) -> None:
        """Timer callback for threading.Timer."""
        self._fire()

    def _fire(self) -> None:
        """Fire the callback with all pending items."""
        with self._lock:
            if not self._pending_items:
                return
            items = self._pending_items
            self._pending_items = []
            self._timer_id = None

        # Call outside the lock to avoid deadlocks
        self._callback(items)

    def flush(self) -> None:
        """Immediately fire any pending items without waiting."""
        with self._lock:
            if self._timer_id is not None:
                if self._cancel_scheduler:
                    self._cancel_scheduler(self._timer_id)  # type: ignore
                elif isinstance(self._timer_id, threading.Timer):
                    self._timer_id.cancel()
                self._timer_id = None
        self._fire()

    def cancel(self) -> None:
        """Cancel any pending items and timer without firing."""
        with self._lock:
            if self._timer_id is not None:
                if self._cancel_scheduler:
                    self._cancel_scheduler(self._timer_id)  # type: ignore
                elif isinstance(self._timer_id, threading.Timer):
                    self._timer_id.cancel()
                self._timer_id = None
            self._pending_items = []

    @property
    def pending_count(self) -> int:
        """Get the number of items waiting to be processed."""
        with self._lock:
            return len(self._pending_items)

    @property
    def has_pending(self) -> bool:
        """Check if there are items waiting to be processed."""
        with self._lock:
            return len(self._pending_items) > 0


class CallDebouncer:
    """
    Debounces rapid calls to a function, only executing once after settling.

    Unlike Debouncer which batches items, this simply ensures a function
    is called once after a period of inactivity.
    """

    def __init__(
        self,
        callback: Callable[[], Any],
        delay_ms: int = 100,
        scheduler: Callable[[Callable[[], bool]], int] | None = None,
        cancel_scheduler: Callable[[int], None] | None = None,
    ) -> None:
        """
        Initialize the call debouncer.

        Args:
            callback: Function to call when debounce fires.
            delay_ms: Milliseconds to wait after last call before firing.
            scheduler: Function to schedule callback (e.g., GLib.timeout_add).
            cancel_scheduler: Function to cancel scheduled callback.
        """
        self._callback = callback
        self._delay_ms = delay_ms
        self._scheduler = scheduler
        self._cancel_scheduler = cancel_scheduler

        self._lock = threading.Lock()
        self._timer_id: int | threading.Timer | None = None
        self._pending = False

    def call(self) -> None:
        """Request a call to the callback (debounced)."""
        with self._lock:
            self._pending = True
            self._reset_timer()

    def _reset_timer(self) -> None:
        """Cancel existing timer and start a new one. Must be called with lock held."""
        if self._timer_id is not None:
            if self._cancel_scheduler:
                self._cancel_scheduler(self._timer_id)  # type: ignore
            elif isinstance(self._timer_id, threading.Timer):
                self._timer_id.cancel()
            self._timer_id = None

        if self._scheduler:
            self._timer_id = self._scheduler(self._on_timer)
        else:
            timer = threading.Timer(self._delay_ms / 1000.0, self._on_timer_thread)
            timer.daemon = True
            timer.start()
            self._timer_id = timer

    def _on_timer(self) -> bool:
        """Timer callback for GLib scheduler."""
        self._fire()
        return False

    def _on_timer_thread(self) -> None:
        """Timer callback for threading.Timer."""
        self._fire()

    def _fire(self) -> None:
        """Fire the callback."""
        with self._lock:
            if not self._pending:
                return
            self._pending = False
            self._timer_id = None

        self._callback()

    def flush(self) -> None:
        """Immediately fire if pending."""
        with self._lock:
            if self._timer_id is not None:
                if self._cancel_scheduler:
                    self._cancel_scheduler(self._timer_id)  # type: ignore
                elif isinstance(self._timer_id, threading.Timer):
                    self._timer_id.cancel()
                self._timer_id = None
        self._fire()

    def cancel(self) -> None:
        """Cancel without firing."""
        with self._lock:
            if self._timer_id is not None:
                if self._cancel_scheduler:
                    self._cancel_scheduler(self._timer_id)  # type: ignore
                elif isinstance(self._timer_id, threading.Timer):
                    self._timer_id.cancel()
                self._timer_id = None
            self._pending = False

    @property
    def is_pending(self) -> bool:
        """Check if a call is pending."""
        with self._lock:
            return self._pending
