"""BlueBubbles Socket.IO client for real-time updates."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import socketio

from .models import Message, Chat


class BlueBubblesSocket:
    """Socket.IO client for real-time BlueBubbles updates."""

    def __init__(self, server_url: str, password: str) -> None:
        self.server_url = server_url.rstrip("/")
        self.password = password
        self._sio: socketio.AsyncClient | None = None
        self._connected = False

        # Event callbacks
        self._on_new_message: Callable[[Message, str], None] | None = None
        self._on_message_updated: Callable[[Message], None] | None = None
        self._on_typing: Callable[[str, bool], None] | None = None
        self._on_connected: Callable[[], None] | None = None
        self._on_disconnected: Callable[[], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def on_new_message(self, callback: Callable[[Message, str], None]) -> None:
        """Register callback for new messages. Args: (message, chat_guid)"""
        self._on_new_message = callback

    def on_message_updated(self, callback: Callable[[Message], None]) -> None:
        """Register callback for message updates (delivered, read, etc)."""
        self._on_message_updated = callback

    def on_typing(self, callback: Callable[[str, bool], None]) -> None:
        """Register callback for typing indicators. Args: (chat_guid, is_typing)"""
        self._on_typing = callback

    def on_connected(self, callback: Callable[[], None]) -> None:
        """Register callback for connection established."""
        self._on_connected = callback

    def on_disconnected(self, callback: Callable[[], None]) -> None:
        """Register callback for disconnection."""
        self._on_disconnected = callback

    def _create_client(self) -> socketio.AsyncClient:
        """Create a new Socket.IO client with event handlers."""
        sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,  # Infinite retries
            reconnection_delay=1,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )

        # Register event handlers
        @sio.event
        async def connect() -> None:
            self._connected = True
            print("Socket.IO connected")
            if self._on_connected:
                self._on_connected()

        @sio.event
        async def disconnect() -> None:
            self._connected = False
            print("Socket.IO disconnected")
            if self._on_disconnected:
                self._on_disconnected()

        @sio.event
        async def connect_error(data: Any) -> None:
            print(f"Socket.IO connection error: {data}")

        # BlueBubbles events - listen to all possible event names
        @sio.on("new-message")
        async def on_new_message(data: dict[str, Any]) -> None:
            await self._handle_new_message(data)

        @sio.on("updated-message")
        async def on_updated_message(data: dict[str, Any]) -> None:
            await self._handle_updated_message(data)

        @sio.on("message-updated")
        async def on_message_updated(data: dict[str, Any]) -> None:
            await self._handle_updated_message(data)

        @sio.on("typing-indicator")
        async def on_typing_indicator(data: dict[str, Any]) -> None:
            await self._handle_typing(data)

        @sio.on("*")
        async def catch_all(event: str, data: Any) -> None:
            print(f"Socket.IO event: {event} -> {str(data)[:200]}")

        return sio

    async def connect(self) -> None:
        """Connect to the BlueBubbles server."""
        if self._sio is not None and self._connected:
            return

        # Try different connection methods
        connection_attempts = [
            # Method 1: Query param with guid
            (f"{self.server_url}?guid={self.password}", {}),
            # Method 2: Query param with password
            (f"{self.server_url}?password={self.password}", {}),
            # Method 3: Auth object
            (self.server_url, {"auth": {"guid": self.password}}),
            # Method 4: Auth object with password key
            (self.server_url, {"auth": {"password": self.password}}),
        ]

        last_error = None
        for i, (url, kwargs) in enumerate(connection_attempts):
            try:
                self._sio = self._create_client()
                print(f"Socket.IO connection attempt {i + 1}: {url[:50]}...")
                await self._sio.connect(
                    url,
                    transports=["websocket", "polling"],
                    wait_timeout=10,
                    **kwargs,
                )
                print(f"Socket.IO connected successfully with method {i + 1}")
                return
            except Exception as e:
                last_error = e
                print(f"Socket.IO connection attempt {i + 1} failed: {e}")
                if self._sio:
                    try:
                        await self._sio.disconnect()
                    except Exception:
                        pass
                    self._sio = None

        raise Exception(f"All Socket.IO connection attempts failed. Last error: {last_error}")

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._sio is not None:
            await self._sio.disconnect()
            self._sio = None
            self._connected = False

    async def _handle_new_message(self, data: dict[str, Any]) -> None:
        """Handle new message event."""
        try:
            # The data structure may vary - handle both direct message and wrapped
            message_data = data.get("data", data)
            if isinstance(message_data, dict):
                message = Message(**message_data)

                # Get chat GUID from the message or chats array
                chat_guid = message.chat_guid
                if not chat_guid and "chats" in message_data:
                    chats = message_data["chats"]
                    if chats and len(chats) > 0:
                        if isinstance(chats[0], dict):
                            chat_guid = chats[0].get("guid")
                        else:
                            chat_guid = str(chats[0])

                if self._on_new_message and chat_guid:
                    self._on_new_message(message, chat_guid)
        except Exception as e:
            print(f"Error handling new message: {e}")
            print(f"Data: {data}")

    async def _handle_updated_message(self, data: dict[str, Any]) -> None:
        """Handle message updated event."""
        try:
            message_data = data.get("data", data)
            if isinstance(message_data, dict):
                message = Message(**message_data)
                if self._on_message_updated:
                    self._on_message_updated(message)
        except Exception as e:
            print(f"Error handling updated message: {e}")

    async def _handle_typing(self, data: dict[str, Any]) -> None:
        """Handle typing indicator event."""
        try:
            chat_guid = data.get("guid") or data.get("chatGuid")
            is_typing = data.get("display", True)
            if self._on_typing and chat_guid:
                self._on_typing(chat_guid, is_typing)
        except Exception as e:
            print(f"Error handling typing indicator: {e}")

    async def wait(self) -> None:
        """Wait for the connection to close."""
        if self._sio:
            await self._sio.wait()
