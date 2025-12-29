"""Pydantic models for BlueBubbles API responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageEffect(str, Enum):
    """iMessage effects."""
    NONE = ""
    SLAM = "com.apple.MobileSMS.expressivesend.impact"
    LOUD = "com.apple.MobileSMS.expressivesend.loud"
    GENTLE = "com.apple.MobileSMS.expressivesend.gentle"
    INVISIBLE_INK = "com.apple.MobileSMS.expressivesend.invisibleink"
    ECHO = "com.apple.messages.effect.CKEchoEffect"
    SPOTLIGHT = "com.apple.messages.effect.CKSpotlightEffect"
    BALLOONS = "com.apple.messages.effect.CKHappyBirthdayEffect"
    CONFETTI = "com.apple.messages.effect.CKConfettiEffect"
    LOVE = "com.apple.messages.effect.CKHeartEffect"
    LASERS = "com.apple.messages.effect.CKLasersEffect"
    FIREWORKS = "com.apple.messages.effect.CKFireworksEffect"
    CELEBRATION = "com.apple.messages.effect.CKSparklesEffect"


class TapbackType(int, Enum):
    """iMessage reaction/tapback types."""
    LOVE = 2000
    LIKE = 2001
    DISLIKE = 2002
    LAUGH = 2003
    EMPHASIZE = 2004
    QUESTION = 2005
    # Remove reactions
    REMOVE_LOVE = 3000
    REMOVE_LIKE = 3001
    REMOVE_DISLIKE = 3002
    REMOVE_LAUGH = 3003
    REMOVE_EMPHASIZE = 3004
    REMOVE_QUESTION = 3005


class Handle(BaseModel):
    """Represents a contact/phone number."""

    model_config = ConfigDict(populate_by_name=True)

    original_row_id: int = Field(alias="originalROWID")
    address: str
    country: str | None = None
    service: str = "iMessage"
    uncanonical_id: str | None = Field(default=None, alias="uncanonicalizedId")


class Contact(BaseModel):
    """Represents a contact from the address book."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    nickname: str | None = None
    birthday: str | None = None
    phones: list[dict[str, Any]] = Field(default_factory=list, alias="phoneNumbers")
    emails: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def name(self) -> str | None:
        """Get the best display name for this contact."""
        if self.display_name:
            return self.display_name
        if self.first_name or self.last_name:
            parts = [self.first_name, self.last_name]
            return " ".join(p for p in parts if p)
        if self.nickname:
            return self.nickname
        return None


class Attachment(BaseModel):
    """Represents a message attachment (image, file, etc.)."""

    model_config = ConfigDict(populate_by_name=True)

    original_row_id: int = Field(alias="originalROWID")
    guid: str
    uti: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    transfer_name: str | None = Field(default=None, alias="transferName")
    total_bytes: int = Field(default=0, alias="totalBytes")
    height: int | None = None
    width: int | None = None
    is_sticker: bool = Field(default=False, alias="isSticker")
    hide_attachment: bool = Field(default=False, alias="hideAttachment")
    blurhash: str | None = None

    @property
    def is_image(self) -> bool:
        """Check if attachment is an image."""
        if self.mime_type:
            return self.mime_type.startswith("image/")
        if self.uti:
            return "image" in self.uti.lower()
        return False

    @property
    def is_video(self) -> bool:
        """Check if attachment is a video."""
        if self.mime_type:
            return self.mime_type.startswith("video/")
        if self.uti:
            return "video" in self.uti.lower() or "movie" in self.uti.lower()
        return False


class Message(BaseModel):
    """Represents an iMessage/SMS message."""

    model_config = ConfigDict(populate_by_name=True)

    original_row_id: int = Field(alias="originalROWID")
    guid: str
    text: str | None = None
    subject: str | None = None
    country: str | None = None
    is_from_me: bool = Field(alias="isFromMe")
    is_delayed: bool = Field(default=False, alias="isDelayed")
    is_auto_reply: bool = Field(default=False, alias="isAutoReply")
    is_system_message: bool = Field(default=False, alias="isSystemMessage")
    is_forward: bool = Field(default=False, alias="isForward")
    is_archived: bool = Field(default=False, alias="isArchived")
    has_dd_results: bool = Field(default=False, alias="hasDdResults")
    cache_roomnames: str | None = Field(default=None, alias="cacheRoomnames")
    is_audio_message: bool = Field(default=False, alias="isAudioMessage")
    date_created: int = Field(alias="dateCreated")
    date_read: int | None = Field(default=None, alias="dateRead")
    date_delivered: int | None = Field(default=None, alias="dateDelivered")
    is_sent: bool = Field(default=False, alias="isSent")
    is_delivered: bool = Field(default=False, alias="isDelivered")
    is_read: bool = Field(default=False, alias="isRead")
    has_attachments: bool = Field(default=False, alias="hasAttachments")
    attachments: list[Attachment] = Field(default_factory=list)
    associated_message_guid: str | None = Field(default=None, alias="associatedMessageGuid")
    associated_message_type: int | None = Field(default=None, alias="associatedMessageType")
    expression_id: str | None = Field(default=None, alias="expressiveSendStyleId")
    thread_originator_guid: str | None = Field(default=None, alias="threadOriginatorGuid")
    thread_originator_part: str | None = Field(default=None, alias="threadOriginatorPart")
    handle: Handle | None = None
    handle_id: int = Field(default=0, alias="handleId")
    chat_guid: str | None = Field(default=None, alias="chats")
    error: int = 0

    @field_validator("chat_guid", mode="before")
    @classmethod
    def extract_chat_guid(cls, v: Any) -> str | None:
        """Extract chat GUID from chats array if present."""
        if isinstance(v, list):
            if len(v) == 0:
                return None
            if isinstance(v[0], dict):
                return v[0].get("guid")
            return str(v[0])
        if v is None or v == "":
            return None
        return v

    @field_validator("associated_message_type", mode="before")
    @classmethod
    def parse_associated_message_type(cls, v: Any) -> int | None:
        """Convert string reaction types to integers."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            # Map string names to integer codes
            reaction_map = {
                "love": 2000,
                "like": 2001,
                "dislike": 2002,
                "laugh": 2003,
                "emphasize": 2004,
                "question": 2005,
                # Remove reactions
                "-love": 3000,
                "-like": 3001,
                "-dislike": 3002,
                "-laugh": 3003,
                "-emphasize": 3004,
                "-question": 3005,
            }
            lower_v = v.lower().strip()
            if lower_v in reaction_map:
                return reaction_map[lower_v]
            # Try parsing as integer string
            try:
                return int(v)
            except ValueError:
                return None
        return None

    @property
    def date_created_dt(self) -> datetime:
        """Get date_created as datetime object."""
        # BlueBubbles uses milliseconds since epoch
        return datetime.fromtimestamp(self.date_created / 1000)

    @property
    def is_reaction(self) -> bool:
        """Check if this message is a reaction/tapback."""
        return self.associated_message_type is not None and self.associated_message_type >= 2000

    @property
    def tapback_type(self) -> TapbackType | None:
        """Get the tapback type if this is a reaction."""
        if self.associated_message_type is not None:
            try:
                return TapbackType(self.associated_message_type)
            except ValueError:
                return None
        return None


class Chat(BaseModel):
    """Represents a conversation/chat."""

    model_config = ConfigDict(populate_by_name=True)

    original_row_id: int = Field(alias="originalROWID")
    guid: str
    chat_identifier: str = Field(alias="chatIdentifier")
    display_name: str | None = Field(default=None, alias="displayName")
    is_archived: bool = Field(default=False, alias="isArchived")
    is_filtered: bool = Field(default=False, alias="isFiltered")
    is_group: bool = Field(default=False, alias="isGroup")
    participants: list[Handle] = Field(default_factory=list)
    last_message: Message | None = Field(default=None, alias="lastMessage")

    @property
    def title(self) -> str:
        """Get display title for the chat."""
        if self.display_name:
            return self.display_name
        if self.participants:
            if len(self.participants) == 1:
                return self.participants[0].address
            return ", ".join(p.address for p in self.participants[:3])
        return self.chat_identifier


class ServerInfo(BaseModel):
    """BlueBubbles server information."""

    model_config = ConfigDict(populate_by_name=True)

    os_version: str = Field(alias="os_version")
    server_version: str = Field(alias="server_version")
    private_api: bool = Field(alias="private_api")
    proxy_service: str = Field(alias="proxy_service")
    helper_connected: bool = Field(alias="helper_connected")
    detected_icloud: str | None = Field(default=None, alias="detected_icloud")


class ApiResponse(BaseModel):
    """Standard BlueBubbles API response wrapper."""

    status: int
    message: str
    data: Any = None
    error: dict[str, str] | None = None

    @property
    def is_success(self) -> bool:
        """Check if the response indicates success."""
        return 200 <= self.status < 300
