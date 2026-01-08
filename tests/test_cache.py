"""Tests for the SQLite cache."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from bluebubbles_linux.state.cache import Cache
from bluebubbles_linux.api.models import Chat, Handle, Message


class TestCacheWALMode:
    """Test that the cache uses WAL mode for better concurrency."""

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        """Verify WAL mode is enabled on the database."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        conn = cache._get_conn()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]

        assert mode.lower() == "wal"
        cache.close()

    def test_synchronous_normal(self, tmp_path: Path) -> None:
        """Verify synchronous mode is NORMAL (1) for WAL performance."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        conn = cache._get_conn()
        cursor = conn.execute("PRAGMA synchronous")
        sync_mode = cursor.fetchone()[0]

        # NORMAL = 1
        assert sync_mode == 1
        cache.close()

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        """Verify foreign keys are enabled."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        conn = cache._get_conn()
        cursor = conn.execute("PRAGMA foreign_keys")
        fk_enabled = cursor.fetchone()[0]

        assert fk_enabled == 1
        cache.close()


class TestCacheConcurrency:
    """Test concurrent access to the cache."""

    def test_concurrent_reads(self, tmp_path: Path) -> None:
        """Multiple threads can read simultaneously."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        # Add some test data
        chat = Chat(
            originalROWID=1,
            guid="test-chat-1",
            chatIdentifier="test@example.com",
            displayName="Test Chat",
            isArchived=False,
            isFiltered=False,
            isGroup=False,
            participants=[],
        )
        cache.save_chat(chat)

        results: list[Chat | None] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def read_chat() -> None:
            try:
                result = cache.get_chat("test-chat-1")
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        # Start multiple read threads
        threads = [threading.Thread(target=read_chat) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent reads: {errors}"
        assert len(results) == 10
        assert all(r is not None and r.guid == "test-chat-1" for r in results)
        cache.close()

    def test_concurrent_read_write(self, tmp_path: Path) -> None:
        """Reads can proceed while writes are happening (WAL mode benefit)."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        # Add initial data
        for i in range(5):
            chat = Chat(
                originalROWID=i,
                guid=f"chat-{i}",
                chatIdentifier=f"test{i}@example.com",
                displayName=f"Chat {i}",
                isArchived=False,
                isFiltered=False,
                isGroup=False,
                participants=[],
            )
            cache.save_chat(chat)

        read_results: list[int] = []
        write_results: list[bool] = []
        errors: list[Exception] = []

        def reader() -> None:
            """Continuously read chats."""
            try:
                for _ in range(20):
                    chats = cache.get_all_chats()
                    read_results.append(len(chats))
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        def writer() -> None:
            """Continuously write new chats."""
            try:
                for i in range(10):
                    chat = Chat(
                        originalROWID=100 + i,
                        guid=f"new-chat-{i}",
                        chatIdentifier=f"new{i}@example.com",
                        displayName=f"New Chat {i}",
                        isArchived=False,
                        isFiltered=False,
                        isGroup=False,
                        participants=[],
                    )
                    cache.save_chat(chat)
                    write_results.append(True)
                    time.sleep(0.02)
            except Exception as e:
                errors.append(e)

        # Start reader and writer threads
        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)

        reader_thread.start()
        writer_thread.start()

        reader_thread.join()
        writer_thread.join()

        assert len(errors) == 0, f"Errors during concurrent read/write: {errors}"
        assert len(read_results) == 20  # All reads completed
        assert len(write_results) == 10  # All writes completed
        cache.close()


class TestCacheChatOperations:
    """Test chat CRUD operations."""

    def test_save_and_get_chat(self, tmp_path: Path) -> None:
        """Save and retrieve a chat."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        chat = Chat(
            originalROWID=1,
            guid="test-guid",
            chatIdentifier="+1234567890",
            displayName="Test User",
            isArchived=False,
            isFiltered=False,
            isGroup=False,
            participants=[
                Handle(originalROWID=1, address="+1234567890", service="iMessage")
            ],
        )
        cache.save_chat(chat)

        retrieved = cache.get_chat("test-guid")
        assert retrieved is not None
        assert retrieved.guid == "test-guid"
        assert retrieved.display_name == "Test User"
        assert len(retrieved.participants) == 1
        cache.close()

    def test_get_all_chats_ordered_by_date(self, tmp_path: Path) -> None:
        """Chats are returned ordered by last message date."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        # Create chats with different last message dates
        for i, date in enumerate([1000, 3000, 2000]):
            msg = Message(
                originalROWID=i,
                guid=f"msg-{i}",
                text=f"Message {i}",
                isFromMe=True,
                dateCreated=date,
                isSent=True,
                isDelivered=True,
                isRead=False,
                handleId=1,
                hasAttachments=False,
            )
            chat = Chat(
                originalROWID=i,
                guid=f"chat-{i}",
                chatIdentifier=f"id-{i}",
                displayName=f"Chat {i}",
                isArchived=False,
                isFiltered=False,
                isGroup=False,
                participants=[],
                lastMessage=msg,
            )
            cache.save_chat(chat)

        chats = cache.get_all_chats()
        assert len(chats) == 3
        # Should be ordered by date DESC: 3000, 2000, 1000
        assert chats[0].guid == "chat-1"  # date 3000
        assert chats[1].guid == "chat-2"  # date 2000
        assert chats[2].guid == "chat-0"  # date 1000
        cache.close()

    def test_chat_count(self, tmp_path: Path) -> None:
        """Get correct chat count."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        assert cache.get_chat_count() == 0

        for i in range(3):
            chat = Chat(
                originalROWID=i,
                guid=f"chat-{i}",
                chatIdentifier=f"id-{i}",
                displayName=f"Chat {i}",
                isArchived=False,
                isFiltered=False,
                isGroup=False,
                participants=[],
            )
            cache.save_chat(chat)

        assert cache.get_chat_count() == 3
        cache.close()


class TestCacheMessageOperations:
    """Test message CRUD operations."""

    def test_save_and_get_messages(self, tmp_path: Path) -> None:
        """Save and retrieve messages for a chat."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        # First create a chat
        chat = Chat(
            originalROWID=1,
            guid="chat-1",
            chatIdentifier="test",
            displayName="Test",
            isArchived=False,
            isFiltered=False,
            isGroup=False,
            participants=[],
        )
        cache.save_chat(chat)

        # Save messages
        messages = [
            Message(
                originalROWID=i,
                guid=f"msg-{i}",
                text=f"Message {i}",
                isFromMe=i % 2 == 0,
                dateCreated=1000 + i * 100,
                isSent=True,
                isDelivered=True,
                isRead=False,
                handleId=1,
                hasAttachments=False,
            )
            for i in range(5)
        ]
        cache.save_messages("chat-1", messages)

        # Retrieve messages
        retrieved = cache.get_chat_messages("chat-1", limit=10)
        assert len(retrieved) == 5
        # Should be ordered by date DESC
        assert retrieved[0].guid == "msg-4"
        assert retrieved[4].guid == "msg-0"
        cache.close()


class TestCacheContactOperations:
    """Test contact operations."""

    def test_save_and_get_contacts(self, tmp_path: Path) -> None:
        """Save and retrieve contacts."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        contacts = {
            "+1234567890": "John Doe",
            "+0987654321": "Jane Smith",
            "test@example.com": "Test User",
        }
        cache.save_contacts(contacts)

        retrieved = cache.get_all_contacts()
        assert len(retrieved) == 3
        assert retrieved["+1234567890"] == "John Doe"
        assert retrieved["+0987654321"] == "Jane Smith"
        cache.close()

    def test_get_single_contact(self, tmp_path: Path) -> None:
        """Get a single contact by address."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        contacts = {"+1234567890": "John Doe"}
        cache.save_contacts(contacts)

        assert cache.get_contact("+1234567890") == "John Doe"
        assert cache.get_contact("nonexistent") is None
        cache.close()


class TestCacheClearAll:
    """Test clearing all cached data."""

    def test_clear_all(self, tmp_path: Path) -> None:
        """Clear all removes all data."""
        db_path = tmp_path / "test.db"
        cache = Cache(db_path)

        # Add some data
        chat = Chat(
            originalROWID=1,
            guid="chat-1",
            chatIdentifier="test",
            displayName="Test",
            isArchived=False,
            isFiltered=False,
            isGroup=False,
            participants=[],
        )
        cache.save_chat(chat)
        cache.save_contacts({"+123": "Test"})

        assert cache.get_chat_count() == 1
        assert len(cache.get_all_contacts()) == 1

        cache.clear_all()

        assert cache.get_chat_count() == 0
        assert len(cache.get_all_contacts()) == 0
        cache.close()
