"""BlueBubbles REST API client."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

import httpx

from .models import ApiResponse, Attachment, Chat, Contact, Handle, Message, ServerInfo


class BlueBubblesError(Exception):
    """Base exception for BlueBubbles API errors."""

    def __init__(self, message: str, status: int = 0, error_type: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class ConnectionError(BlueBubblesError):
    """Failed to connect to the server."""
    pass


class AuthenticationError(BlueBubblesError):
    """Authentication failed."""
    pass


class BlueBubblesClient:
    """Async client for the BlueBubbles REST API."""

    def __init__(self, server_url: str, password: str, timeout: float = 30.0):
        """
        Initialize the client.

        Args:
            server_url: Base URL of the BlueBubbles server (e.g., https://example.ngrok.io)
            password: Server password for authentication
            timeout: Request timeout in seconds
        """
        self.server_url = server_url.rstrip("/")
        self.password = password
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BlueBubblesClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        if self._client is not None:
            return  # Already connected
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            follow_redirects=True,
            http2=True,  # Enable HTTP/2 for better connection reuse
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get the HTTP client, raising if not connected."""
        if self._client is None:
            raise BlueBubblesError("Client not connected. Call connect() first.")
        return self._client

    def _build_url(self, endpoint: str, **params: Any) -> str:
        """Build a full URL with authentication."""
        # Add password to params
        params["password"] = self.password
        # Filter out None values
        params = {k: v for k, v in params.items() if v is not None}
        query = urlencode(params)
        return f"{self.server_url}/api/v1/{endpoint.lstrip('/')}?{query}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> ApiResponse:
        """Make an API request."""
        params = params or {}
        url = self._build_url(endpoint, **params)

        try:
            response = await self.client.request(
                method,
                url,
                json=json_data,
            )
        except httpx.ConnectError as e:
            raise ConnectionError(f"Failed to connect to server: {e}") from e
        except httpx.TimeoutException as e:
            raise ConnectionError(f"Request timed out: {e}") from e

        # Check for HTTP errors first
        if response.status_code >= 400:
            raise BlueBubblesError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                status=response.status_code,
            )

        try:
            data = response.json()
            api_response = ApiResponse(**data)
        except Exception as e:
            # Include response details for debugging
            raise BlueBubblesError(
                f"Failed to parse response (status={response.status_code}): {e}. "
                f"Response: {response.text[:500]}"
            ) from e

        if api_response.status == 401:
            raise AuthenticationError("Invalid password", status=401)

        if not api_response.is_success:
            error_msg = api_response.message
            error_type = None
            if api_response.error:
                error_msg = api_response.error.get("error", error_msg)
                error_type = api_response.error.get("type")
            raise BlueBubblesError(error_msg, status=api_response.status, error_type=error_type)

        return api_response

    async def _get(self, endpoint: str, **params: Any) -> ApiResponse:
        """Make a GET request."""
        return await self._request("GET", endpoint, params=params)

    async def _post(
        self, endpoint: str, data: dict[str, Any] | None = None, **params: Any
    ) -> ApiResponse:
        """Make a POST request."""
        return await self._request("POST", endpoint, params=params, json_data=data)

    # Server endpoints

    async def ping(self) -> bool:
        """Test server connectivity."""
        try:
            response = await self._get("ping")
            return response.is_success
        except BlueBubblesError:
            return False

    async def get_server_info(self) -> ServerInfo:
        """Get server information."""
        response = await self._get("server/info")
        return ServerInfo(**response.data)

    # Chat endpoints

    async def get_chats(
        self,
        limit: int = 25,
        offset: int = 0,
        with_participants: bool = True,
        with_last_message: bool = True,
        sort: str = "lastmessage",
    ) -> list[Chat]:
        """
        Get all chats.

        Args:
            limit: Maximum number of chats to return
            offset: Offset for pagination
            with_participants: Include participant handles
            with_last_message: Include the last message in each chat
            sort: Sort order (lastmessage, etc.)
        """
        includes = []
        if with_participants:
            includes.append("participants")
        if with_last_message:
            includes.append("lastmessage")  # Must be lowercase for server to recognize

        # BlueBubbles uses POST /chat/query for listing chats
        data: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sort": sort,
        }
        if includes:
            data["with"] = includes

        response = await self._post("chat/query", data)
        return [Chat(**chat) for chat in response.data]

    async def get_chat(self, chat_guid: str) -> Chat:
        """Get a single chat by GUID."""
        response = await self._get(f"chat/{chat_guid}")
        return Chat(**response.data)

    async def get_chat_messages(
        self,
        chat_guid: str,
        limit: int = 50,
        offset: int = 0,
        after: int | None = None,
        before: int | None = None,
        sort: str = "DESC",
        with_attachments: bool = True,
        with_handle: bool = True,
    ) -> list[Message]:
        """
        Get messages for a chat.

        Args:
            chat_guid: Chat GUID
            limit: Maximum messages to return
            offset: Offset for pagination
            after: Only messages after this timestamp (ms since epoch)
            before: Only messages before this timestamp (ms since epoch)
            sort: Sort order (ASC or DESC)
            with_attachments: Include attachment data
            with_handle: Include sender handle info
        """
        with_includes = []
        if with_attachments:
            with_includes.append("attachment")
        if with_handle:
            with_includes.append("handle")

        response = await self._get(
            f"chat/{chat_guid}/message",
            limit=limit,
            offset=offset,
            after=after,
            before=before,
            sort=sort,
            **{"with": ",".join(with_includes)} if with_includes else {},
        )
        return [Message(**msg) for msg in response.data]

    # Message endpoints

    async def send_message(
        self,
        chat_guid: str,
        text: str,
        method: str = "private-api",
        effect: str | None = None,
        subject: str | None = None,
        reply_to_guid: str | None = None,
    ) -> Message:
        """
        Send a text message.

        Args:
            chat_guid: Target chat GUID
            text: Message text
            method: Send method (private-api or apple-script)
            effect: Optional message effect
            subject: Optional subject line
            reply_to_guid: GUID of message to reply to (for threads)
        """
        data: dict[str, Any] = {
            "chatGuid": chat_guid,
            "message": text,
            "method": method,
        }
        if effect:
            data["effectId"] = effect
        if subject:
            data["subject"] = subject
        if reply_to_guid:
            data["selectedMessageGuid"] = reply_to_guid

        response = await self._post("message/text", data)
        return Message(**response.data)

    async def send_reaction(
        self,
        chat_guid: str,
        message_guid: str,
        reaction: str,
        part_index: int = 0,
    ) -> Message:
        """
        Send a reaction/tapback to a message.

        Args:
            chat_guid: Chat GUID
            message_guid: Target message GUID
            reaction: Tapback type as string ("love", "like", "dislike", "laugh", "emphasize", "question")
                      Prefix with "-" to remove (e.g., "-love")
            part_index: Message part index (for messages with multiple parts)
        """
        data = {
            "chatGuid": chat_guid,
            "selectedMessageGuid": message_guid,
            "reaction": reaction,
            "partIndex": part_index,
        }
        response = await self._post("message/react", data)
        return Message(**response.data)

    async def edit_message(
        self,
        message_guid: str,
        new_text: str,
        backtrack_count: int = 1,
        part_index: int = 0,
    ) -> Message:
        """
        Edit a sent message (requires Private API and macOS 13+/iOS 16+).

        Args:
            message_guid: GUID of message to edit
            new_text: New message text
            backtrack_count: Edit history index (1 = first edit)
            part_index: Message part index
        """
        data = {
            "editedMessage": new_text,
            "backtrackCount": backtrack_count,
            "partIndex": part_index,
        }
        response = await self._post(f"message/{message_guid}/edit", data)
        return Message(**response.data)

    async def send_typing(self, chat_guid: str, is_typing: bool = True) -> bool:
        """
        Send typing indicator.

        Args:
            chat_guid: Chat GUID
            is_typing: True to show typing, False to stop
        """
        data = {
            "chatGuid": chat_guid,
        }
        endpoint = "chat/typing" if is_typing else "chat/stop-typing"
        response = await self._post(endpoint, data)
        return response.is_success

    async def mark_chat_read(self, chat_guid: str) -> bool:
        """Mark a chat as read."""
        data = {"chatGuid": chat_guid}
        response = await self._post("chat/read", data)
        return response.is_success

    # Handle endpoints

    async def get_handles(self, limit: int = 100, offset: int = 0) -> list[Handle]:
        """Get all handles/contacts."""
        response = await self._get("handle", limit=limit, offset=offset)
        return [Handle(**h) for h in response.data]

    # Attachment endpoints

    async def get_attachment(self, attachment_guid: str) -> bytes:
        """Download an attachment by GUID."""
        url = self._build_url(f"attachment/{attachment_guid}/download")
        response = await self.client.get(url)
        if response.status_code != 200:
            raise BlueBubblesError(f"Failed to download attachment: {response.status_code}")
        return response.content

    async def get_attachment_info(self, attachment_guid: str) -> Attachment:
        """Get attachment metadata."""
        response = await self._get(f"attachment/{attachment_guid}")
        return Attachment(**response.data)

    # Contact endpoints

    async def query_contacts(self, addresses: list[str]) -> list[Contact]:
        """
        Query contacts by phone numbers or email addresses.

        Args:
            addresses: List of phone numbers or emails to look up
        """
        data = {"addresses": addresses}
        try:
            response = await self._post("contact/query", data)
            if response.data:
                return [Contact(**c) for c in response.data]
        except BlueBubblesError as e:
            # Contact query may not be available on older servers
            print(f"Contact query failed: {e}")
        return []

    async def get_contacts(self) -> list[Contact]:
        """Get all contacts from the server."""
        try:
            response = await self._get("contact")
            if response.data:
                return [Contact(**c) for c in response.data]
        except BlueBubblesError as e:
            print(f"Get contacts failed: {e}")
        return []


# Convenience function for quick testing
async def test_connection(server_url: str, password: str) -> tuple[bool, str]:
    """
    Test connection to a BlueBubbles server.

    Returns:
        Tuple of (success, message)
    """
    try:
        async with BlueBubblesClient(server_url, password) as client:
            if await client.ping():
                info = await client.get_server_info()
                return True, f"Connected to BlueBubbles {info.server_version}"
            return False, "Server ping failed"
    except AuthenticationError:
        return False, "Invalid password"
    except ConnectionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error: {e}"
