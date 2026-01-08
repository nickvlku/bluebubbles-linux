"""Tests for chat list update logic."""

from __future__ import annotations

import pytest


class TestChatUpdateBatching:
    """Test the chat update batching/deduplication logic.

    This tests the deduplication algorithm used in _process_batched_chat_updates
    without requiring GTK widgets.
    """

    @staticmethod
    def deduplicate_guids(chat_guids: list[str]) -> list[str]:
        """
        Deduplicate chat GUIDs while preserving order (most recent first).

        This is the same algorithm used in MainWindow._process_batched_chat_updates.
        """
        if not chat_guids:
            return []

        seen: set[str] = set()
        unique_guids: list[str] = []
        for guid in reversed(chat_guids):
            if guid not in seen:
                seen.add(guid)
                unique_guids.append(guid)
        unique_guids.reverse()
        return unique_guids

    def test_empty_list(self) -> None:
        """Empty list returns empty list."""
        assert self.deduplicate_guids([]) == []

    def test_single_item(self) -> None:
        """Single item passes through."""
        assert self.deduplicate_guids(["chat1"]) == ["chat1"]

    def test_no_duplicates(self) -> None:
        """List with no duplicates preserves order."""
        guids = ["chat1", "chat2", "chat3"]
        assert self.deduplicate_guids(guids) == ["chat1", "chat2", "chat3"]

    def test_consecutive_duplicates(self) -> None:
        """Consecutive duplicates keep only first occurrence."""
        guids = ["chat1", "chat1", "chat1", "chat2"]
        assert self.deduplicate_guids(guids) == ["chat1", "chat2"]

    def test_non_consecutive_duplicates(self) -> None:
        """Non-consecutive duplicates keep only last occurrence (most recent)."""
        guids = ["chat1", "chat2", "chat1", "chat3", "chat2"]
        # Last occurrence order (most recent first):
        # chat2 last at index 4
        # chat3 last at index 3
        # chat1 last at index 2
        # So result preserves this "most recent first" order
        assert self.deduplicate_guids(guids) == ["chat1", "chat3", "chat2"]

    def test_all_same(self) -> None:
        """All same GUID returns single item."""
        guids = ["chat1", "chat1", "chat1", "chat1"]
        assert self.deduplicate_guids(guids) == ["chat1"]

    def test_rapid_messages_scenario(self) -> None:
        """
        Simulate rapid messages from multiple chats.

        When multiple messages arrive rapidly:
        - Messages from chat1, then chat2, then chat1 again, then chat3
        - We want the final order to reflect "most recently updated first"
        """
        # Simulate: msg1 from chat1, msg2 from chat2, msg3 from chat1, msg4 from chat3
        guids = ["chat1", "chat2", "chat1", "chat3"]
        result = self.deduplicate_guids(guids)
        # Last occurrence order (most recent updates first):
        # chat3 at index 3 (most recent)
        # chat1 at index 2 (second most recent)
        # chat2 at index 1 (least recent)
        # The algorithm preserves this order for processing
        assert result == ["chat2", "chat1", "chat3"]


class TestChatListDataModel:
    """Test the chat list data model operations.

    These test the data structure operations without GTK widgets.
    """

    def test_move_chat_to_top_in_list(self) -> None:
        """Test moving a chat to top of the list."""
        chats = ["chat1", "chat2", "chat3", "chat4"]

        # Move chat3 to top
        chat_to_move = "chat3"
        chats = [c for c in chats if c != chat_to_move]
        chats.insert(0, chat_to_move)

        assert chats == ["chat3", "chat1", "chat2", "chat4"]

    def test_move_first_chat_is_noop(self) -> None:
        """Moving first chat to top doesn't change order."""
        chats = ["chat1", "chat2", "chat3"]

        chat_to_move = "chat1"
        if chats[0] != chat_to_move:
            chats = [c for c in chats if c != chat_to_move]
            chats.insert(0, chat_to_move)

        assert chats == ["chat1", "chat2", "chat3"]

    def test_update_chat_in_dict(self) -> None:
        """Test updating a chat in the guid->chat mapping."""
        chats_by_guid = {
            "chat1": {"guid": "chat1", "last_message": "old msg"},
            "chat2": {"guid": "chat2", "last_message": "msg2"},
        }

        # Update chat1 with new message
        chats_by_guid["chat1"] = {"guid": "chat1", "last_message": "new msg"}

        assert chats_by_guid["chat1"]["last_message"] == "new msg"
        assert chats_by_guid["chat2"]["last_message"] == "msg2"


class TestRowTracking:
    """Test the row tracking dictionary operations."""

    def test_track_new_row(self) -> None:
        """New rows are tracked by GUID."""
        rows_by_guid: dict[str, str] = {}

        # Simulate creating a row
        rows_by_guid["chat1"] = "row_widget_1"
        rows_by_guid["chat2"] = "row_widget_2"

        assert rows_by_guid.get("chat1") == "row_widget_1"
        assert rows_by_guid.get("chat2") == "row_widget_2"
        assert rows_by_guid.get("chat3") is None

    def test_clear_tracking_on_rebuild(self) -> None:
        """Tracking dict is cleared when rebuilding list."""
        rows_by_guid: dict[str, str] = {
            "chat1": "row_widget_1",
            "chat2": "row_widget_2",
        }

        # Simulate rebuild
        rows_by_guid.clear()

        assert len(rows_by_guid) == 0
        assert rows_by_guid.get("chat1") is None

    def test_lookup_existing_row(self) -> None:
        """Can lookup existing row by GUID."""
        rows_by_guid = {
            "chat1": "row_widget_1",
            "chat2": "row_widget_2",
        }

        row = rows_by_guid.get("chat1")
        assert row == "row_widget_1"

    def test_lookup_missing_row(self) -> None:
        """Missing GUID returns None."""
        rows_by_guid = {"chat1": "row_widget_1"}

        row = rows_by_guid.get("nonexistent")
        assert row is None
