"""Tests for the BlueBubbles API client."""

import pytest
from bluebubbles_linux.api.models import Chat, Handle, Message, Attachment


class TestModels:
    """Test Pydantic model parsing."""

    def test_handle_parsing(self) -> None:
        """Test Handle model parsing."""
        data = {
            "originalROWID": 1,
            "address": "+1234567890",
            "country": "US",
            "service": "iMessage",
        }
        handle = Handle(**data)
        assert handle.address == "+1234567890"
        assert handle.country == "US"

    def test_chat_parsing(self) -> None:
        """Test Chat model parsing."""
        data = {
            "originalROWID": 1,
            "guid": "iMessage;-;+1234567890",
            "chatIdentifier": "+1234567890",
            "displayName": None,
            "isArchived": False,
            "isFiltered": False,
            "isGroup": False,
            "participants": [
                {
                    "originalROWID": 1,
                    "address": "+1234567890",
                    "service": "iMessage",
                }
            ],
        }
        chat = Chat(**data)
        assert chat.guid == "iMessage;-;+1234567890"
        assert len(chat.participants) == 1
        assert chat.title == "+1234567890"

    def test_message_parsing(self) -> None:
        """Test Message model parsing."""
        data = {
            "originalROWID": 100,
            "guid": "msg-guid-123",
            "text": "Hello, world!",
            "isFromMe": True,
            "dateCreated": 1704153600000,  # 2024-01-02 00:00:00 UTC (avoids timezone edge case)
            "isSent": True,
            "isDelivered": True,
            "isRead": False,
            "handleId": 1,
            "hasAttachments": False,
        }
        message = Message(**data)
        assert message.text == "Hello, world!"
        assert message.is_from_me is True
        assert message.date_created_dt.year == 2024

    def test_attachment_is_image(self) -> None:
        """Test attachment type detection."""
        data = {
            "originalROWID": 1,
            "guid": "attach-guid-123",
            "mimeType": "image/jpeg",
            "transferName": "photo.jpg",
            "totalBytes": 12345,
        }
        attachment = Attachment(**data)
        assert attachment.is_image is True
        assert attachment.is_video is False

    def test_chat_title_with_display_name(self) -> None:
        """Test chat title prioritizes display name."""
        data = {
            "originalROWID": 1,
            "guid": "iMessage;+;chat123",
            "chatIdentifier": "chat123",
            "displayName": "Family Group",
            "isGroup": True,
            "participants": [],
        }
        chat = Chat(**data)
        assert chat.title == "Family Group"
