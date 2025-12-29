"""SQLite cache for chats and messages."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..api.models import Chat, Handle, Message
from ..utils.config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "cache.db"
ATTACHMENTS_DIR = CONFIG_DIR / "attachments"
SCHEMA_VERSION = 2  # Bumped for contacts table


class Cache:
    """SQLite-based cache for BlueBubbles data."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()

        # Check schema version
        cursor = conn.execute("PRAGMA user_version")
        version = cursor.fetchone()[0]

        if version < SCHEMA_VERSION:
            self._create_schema(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create database tables."""
        conn.executescript("""
            -- Chats table
            CREATE TABLE IF NOT EXISTS chats (
                guid TEXT PRIMARY KEY,
                original_row_id INTEGER,
                chat_identifier TEXT,
                display_name TEXT,
                is_archived INTEGER DEFAULT 0,
                is_filtered INTEGER DEFAULT 0,
                is_group INTEGER DEFAULT 0,
                participants_json TEXT,
                last_message_json TEXT,
                last_message_date INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_chats_last_message_date
                ON chats(last_message_date DESC);

            -- Messages table
            CREATE TABLE IF NOT EXISTS messages (
                guid TEXT PRIMARY KEY,
                chat_guid TEXT,
                original_row_id INTEGER,
                text TEXT,
                is_from_me INTEGER,
                date_created INTEGER,
                date_read INTEGER,
                date_delivered INTEGER,
                is_sent INTEGER DEFAULT 0,
                is_delivered INTEGER DEFAULT 0,
                is_read INTEGER DEFAULT 0,
                has_attachments INTEGER DEFAULT 0,
                handle_json TEXT,
                attachments_json TEXT,
                associated_message_guid TEXT,
                associated_message_type INTEGER,
                full_json TEXT,
                updated_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (chat_guid) REFERENCES chats(guid)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat_date
                ON messages(chat_guid, date_created DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_date
                ON messages(date_created DESC);

            -- Sync state table
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            -- Contacts table (address -> name mapping)
            CREATE TABLE IF NOT EXISTS contacts (
                address TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_contacts_name
                ON contacts(display_name);
        """)

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # Chat operations

    def get_all_chats(self) -> list[Chat]:
        """Get all cached chats, sorted by last message date."""
        conn = self._get_conn()
        cursor = conn.execute("""
            SELECT * FROM chats
            ORDER BY last_message_date DESC NULLS LAST
        """)

        chats = []
        for row in cursor:
            try:
                chat = self._row_to_chat(row)
                chats.append(chat)
            except Exception as e:
                print(f"Error parsing cached chat {row['guid']}: {e}")

        return chats

    def get_chat(self, guid: str) -> Chat | None:
        """Get a single chat by GUID."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT * FROM chats WHERE guid = ?", (guid,))
        row = cursor.fetchone()
        if row:
            return self._row_to_chat(row)
        return None

    def save_chats(self, chats: list[Chat]) -> None:
        """Save or update multiple chats."""
        conn = self._get_conn()
        now = int(datetime.now().timestamp())

        for chat in chats:
            self._save_chat(conn, chat, now)

        conn.commit()

    def save_chat(self, chat: Chat) -> None:
        """Save or update a single chat."""
        conn = self._get_conn()
        now = int(datetime.now().timestamp())
        self._save_chat(conn, chat, now)
        conn.commit()

    def _save_chat(self, conn: sqlite3.Connection, chat: Chat, now: int) -> None:
        """Internal: save chat to database."""
        participants_json = json.dumps([
            h.model_dump(by_alias=True) for h in chat.participants
        ])

        last_message_json = None
        last_message_date = None
        if chat.last_message:
            last_message_json = chat.last_message.model_dump_json(by_alias=True)
            last_message_date = chat.last_message.date_created

        conn.execute("""
            INSERT OR REPLACE INTO chats
            (guid, original_row_id, chat_identifier, display_name,
             is_archived, is_filtered, is_group, participants_json,
             last_message_json, last_message_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chat.guid,
            chat.original_row_id,
            chat.chat_identifier,
            chat.display_name,
            1 if chat.is_archived else 0,
            1 if chat.is_filtered else 0,
            1 if chat.is_group else 0,
            participants_json,
            last_message_json,
            last_message_date,
            now,
        ))

    def _row_to_chat(self, row: sqlite3.Row) -> Chat:
        """Convert database row to Chat model."""
        participants = []
        if row["participants_json"]:
            participants_data = json.loads(row["participants_json"])
            participants = [Handle(**h) for h in participants_data]

        last_message = None
        if row["last_message_json"]:
            last_message_data = json.loads(row["last_message_json"])
            last_message = Message(**last_message_data)

        return Chat(
            originalROWID=row["original_row_id"],
            guid=row["guid"],
            chatIdentifier=row["chat_identifier"],
            displayName=row["display_name"],
            isArchived=bool(row["is_archived"]),
            isFiltered=bool(row["is_filtered"]),
            isGroup=bool(row["is_group"]),
            participants=participants,
            lastMessage=last_message,
        )

    def get_last_message_date(self) -> int | None:
        """Get the most recent message date across all chats."""
        conn = self._get_conn()
        cursor = conn.execute("""
            SELECT MAX(last_message_date) as max_date FROM chats
        """)
        row = cursor.fetchone()
        return row["max_date"] if row and row["max_date"] else None

    def get_chat_count(self) -> int:
        """Get number of cached chats."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) as count FROM chats")
        return cursor.fetchone()["count"]

    # Message operations

    def get_chat_messages(
        self,
        chat_guid: str,
        limit: int = 50,
        before: int | None = None
    ) -> list[Message]:
        """Get cached messages for a chat."""
        conn = self._get_conn()

        if before:
            cursor = conn.execute("""
                SELECT full_json FROM messages
                WHERE chat_guid = ? AND date_created < ?
                ORDER BY date_created DESC
                LIMIT ?
            """, (chat_guid, before, limit))
        else:
            cursor = conn.execute("""
                SELECT full_json FROM messages
                WHERE chat_guid = ?
                ORDER BY date_created DESC
                LIMIT ?
            """, (chat_guid, limit))

        messages = []
        for row in cursor:
            try:
                msg_data = json.loads(row["full_json"])
                messages.append(Message(**msg_data))
            except Exception as e:
                print(f"Error parsing cached message: {e}")

        return messages

    def save_messages(self, chat_guid: str, messages: list[Message]) -> None:
        """Save or update multiple messages."""
        conn = self._get_conn()
        now = int(datetime.now().timestamp())

        for msg in messages:
            self._save_message(conn, chat_guid, msg, now)

        conn.commit()

    def _save_message(
        self,
        conn: sqlite3.Connection,
        chat_guid: str,
        msg: Message,
        now: int
    ) -> None:
        """Internal: save message to database."""
        handle_json = None
        if msg.handle:
            handle_json = msg.handle.model_dump_json(by_alias=True)

        attachments_json = json.dumps([
            a.model_dump(by_alias=True) for a in msg.attachments
        ])

        full_json = msg.model_dump_json(by_alias=True)

        conn.execute("""
            INSERT OR REPLACE INTO messages
            (guid, chat_guid, original_row_id, text, is_from_me,
             date_created, date_read, date_delivered, is_sent, is_delivered,
             is_read, has_attachments, handle_json, attachments_json,
             associated_message_guid, associated_message_type, full_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg.guid,
            chat_guid,
            msg.original_row_id,
            msg.text,
            1 if msg.is_from_me else 0,
            msg.date_created,
            msg.date_read,
            msg.date_delivered,
            1 if msg.is_sent else 0,
            1 if msg.is_delivered else 0,
            1 if msg.is_read else 0,
            1 if msg.has_attachments else 0,
            handle_json,
            attachments_json,
            msg.associated_message_guid,
            msg.associated_message_type,
            full_json,
            now,
        ))

    def get_latest_message_date(self, chat_guid: str) -> int | None:
        """Get the most recent message date for a chat."""
        conn = self._get_conn()
        cursor = conn.execute("""
            SELECT MAX(date_created) as max_date FROM messages
            WHERE chat_guid = ?
        """, (chat_guid,))
        row = cursor.fetchone()
        return row["max_date"] if row and row["max_date"] else None

    # Sync state

    def get_sync_state(self, key: str) -> str | None:
        """Get a sync state value."""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row["value"] if row else None

    def set_sync_state(self, key: str, value: str) -> None:
        """Set a sync state value."""
        conn = self._get_conn()
        now = int(datetime.now().timestamp())
        conn.execute("""
            INSERT OR REPLACE INTO sync_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, now))
        conn.commit()

    def clear_all(self) -> None:
        """Clear all cached data."""
        conn = self._get_conn()
        conn.executescript("""
            DELETE FROM messages;
            DELETE FROM chats;
            DELETE FROM sync_state;
            DELETE FROM contacts;
        """)
        conn.commit()

    # Contact operations

    def get_all_contacts(self) -> dict[str, str]:
        """Get all cached contacts as address -> name mapping."""
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT address, display_name FROM contacts")
            return {row["address"]: row["display_name"] for row in cursor}
        except sqlite3.OperationalError:
            # Table might not exist yet
            return {}

    def save_contacts(self, contacts: dict[str, str]) -> None:
        """Save contacts (address -> name mapping) to cache."""
        conn = self._get_conn()
        now = int(datetime.now().timestamp())

        # Ensure the table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                address TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            )
        """)

        for address, name in contacts.items():
            conn.execute("""
                INSERT OR REPLACE INTO contacts (address, display_name, updated_at)
                VALUES (?, ?, ?)
            """, (address, name, now))

        conn.commit()
        print(f"Saved {len(contacts)} contacts to cache")

    def get_contact(self, address: str) -> str | None:
        """Get a single contact name by address."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT display_name FROM contacts WHERE address = ?",
                (address,)
            )
            row = cursor.fetchone()
            return row["display_name"] if row else None
        except sqlite3.OperationalError:
            return None

    # Attachment caching

    def get_attachment_path(self, attachment_guid: str) -> Path:
        """Get the local file path for an attachment."""
        # Use hash of GUID to create a flat directory structure
        hash_prefix = hashlib.md5(attachment_guid.encode()).hexdigest()[:2]
        return ATTACHMENTS_DIR / hash_prefix / attachment_guid

    def has_attachment(self, attachment_guid: str) -> bool:
        """Check if an attachment is cached locally."""
        return self.get_attachment_path(attachment_guid).exists()

    def save_attachment(self, attachment_guid: str, data: bytes) -> Path:
        """Save attachment data to local cache."""
        path = self.get_attachment_path(attachment_guid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def get_attachment(self, attachment_guid: str) -> bytes | None:
        """Get cached attachment data."""
        path = self.get_attachment_path(attachment_guid)
        if path.exists():
            return path.read_bytes()
        return None
