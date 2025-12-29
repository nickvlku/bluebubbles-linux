"""Main application window."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

if TYPE_CHECKING:
    from ..application import BlueBubblesApplication

from ..api import BlueBubblesClient, Chat, Message, Attachment, BlueBubblesSocket
from ..api.models import TapbackType
from ..state import Cache
from ..utils.links import find_urls, fetch_link_preview, LinkPreview


class MainWindow(Adw.ApplicationWindow):
    """Main application window with conversation list and message view."""

    def __init__(self, application: BlueBubblesApplication) -> None:
        super().__init__(application=application)
        self.app = application
        self._cache = Cache()
        self._chats: list[Chat] = []
        self._chats_by_guid: dict[str, Chat] = {}
        self._selected_chat: Chat | None = None
        self._messages: list[Message] = []
        self._loading_chats: bool = False
        self._socket: BlueBubblesSocket | None = None
        self._socket_thread: threading.Thread | None = None
        self._contacts: dict[str, str] = {}  # address -> display name
        self._message_scroll: Gtk.ScrolledWindow | None = None  # For scroll control
        self._pending_conversation: dict | None = None  # For new conversations

        self._setup_window()
        self._build_ui()
        self._load_chats()
        self._load_contacts()
        self._connect_socket()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.set_title("BlueBubbles")
        self.set_default_size(1000, 700)

    def _build_ui(self) -> None:
        """Build the main UI."""
        # Main layout: navigation split view
        self._split_view = Adw.NavigationSplitView()
        self._split_view.set_min_sidebar_width(280)
        self._split_view.set_max_sidebar_width(400)

        # Sidebar (conversation list)
        sidebar = self._build_sidebar()
        self._split_view.set_sidebar(sidebar)

        # Content (message view)
        content = self._build_content()
        self._split_view.set_content(content)

        # Wrap in toolbar view
        toolbar_view = Adw.ToolbarView()
        toolbar_view.set_content(self._split_view)

        self.set_content(toolbar_view)

    def _build_sidebar(self) -> Adw.NavigationPage:
        """Build the sidebar with conversation list."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Settings", "app.settings")
        menu.append("About", "app.about")
        menu.append("Quit", "app.quit")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        # New message button
        new_button = Gtk.Button(icon_name="list-add-symbolic")
        new_button.set_tooltip_text("New Message")
        new_button.connect("clicked", self._on_new_message_clicked)
        header.pack_start(new_button)

        box.append(header)

        # Search bar
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Search conversations...")
        search_entry.set_margin_start(12)
        search_entry.set_margin_end(12)
        search_entry.set_margin_top(6)
        search_entry.set_margin_bottom(6)
        box.append(search_entry)

        # Conversation list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._chat_list = Gtk.ListBox()
        self._chat_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._chat_list.add_css_class("navigation-sidebar")
        self._chat_list.connect("row-selected", self._on_chat_selected)

        # Placeholder for empty state
        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        placeholder.set_valign(Gtk.Align.CENTER)
        placeholder.set_halign(Gtk.Align.CENTER)

        spinner = Gtk.Spinner()
        spinner.set_spinning(True)
        spinner.set_size_request(32, 32)
        placeholder.append(spinner)

        loading_label = Gtk.Label(label="Loading conversations...")
        loading_label.add_css_class("dim-label")
        placeholder.append(loading_label)

        self._chat_list.set_placeholder(placeholder)

        scrolled.set_child(self._chat_list)
        box.append(scrolled)

        # Status bar for loading progress
        self._status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._status_bar.set_margin_start(12)
        self._status_bar.set_margin_end(12)
        self._status_bar.set_margin_top(8)
        self._status_bar.set_margin_bottom(8)

        self._status_spinner = Gtk.Spinner()
        self._status_spinner.set_size_request(24, 24)
        self._status_bar.append(self._status_spinner)

        self._status_label = Gtk.Label(label="")
        self._status_label.add_css_class("dim-label")
        self._status_label.add_css_class("caption")
        self._status_label.set_hexpand(True)
        self._status_label.set_xalign(0)
        self._status_bar.append(self._status_label)

        self._status_bar.set_visible(False)
        box.append(self._status_bar)

        page = Adw.NavigationPage(title="Conversations", child=box)
        return page

    def _build_content(self) -> Adw.NavigationPage:
        """Build the content area with message view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        self._content_header = Adw.HeaderBar()
        self._content_header.set_show_start_title_buttons(False)

        # Title widget
        self._content_title = Adw.WindowTitle(title="Select a conversation", subtitle="")
        self._content_header.set_title_widget(self._content_title)

        box.append(self._content_header)

        # Message view (scrollable)
        self._message_scroll = Gtk.ScrolledWindow()
        self._message_scroll.set_vexpand(True)
        self._message_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._message_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._message_list.set_margin_start(16)
        self._message_list.set_margin_end(16)
        self._message_list.set_margin_top(16)
        self._message_list.set_margin_bottom(16)

        # Placeholder for no selection
        self._no_chat_placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._no_chat_placeholder.set_valign(Gtk.Align.CENTER)
        self._no_chat_placeholder.set_halign(Gtk.Align.CENTER)
        self._no_chat_placeholder.set_vexpand(True)

        icon = Gtk.Image.new_from_icon_name("chat-bubble-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        self._no_chat_placeholder.append(icon)

        label = Gtk.Label(label="Select a conversation to start messaging")
        label.add_css_class("dim-label")
        self._no_chat_placeholder.append(label)

        self._message_list.append(self._no_chat_placeholder)

        self._message_scroll.set_child(self._message_list)
        box.append(self._message_scroll)

        # Compose box
        self._compose_box = self._build_compose_box()
        self._compose_box.set_visible(False)
        box.append(self._compose_box)

        page = Adw.NavigationPage(title="Messages", child=box)
        return page

    def _build_compose_box(self) -> Gtk.Box:
        """Build the message compose box."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(12)

        # Attachment button
        attach_button = Gtk.Button(icon_name="mail-attachment-symbolic")
        attach_button.add_css_class("flat")
        attach_button.set_tooltip_text("Add attachment")
        box.append(attach_button)

        # Text entry
        self._message_entry = Gtk.Entry()
        self._message_entry.set_hexpand(True)
        self._message_entry.set_placeholder_text("Type a message...")
        self._message_entry.connect("activate", self._on_send_message)
        box.append(self._message_entry)

        # Send button
        send_button = Gtk.Button(icon_name="go-up-symbolic")
        send_button.add_css_class("suggested-action")
        send_button.add_css_class("circular")
        send_button.set_tooltip_text("Send message")
        send_button.connect("clicked", self._on_send_message)
        box.append(send_button)

        return box

    def _get_chat_title(self, chat: Chat) -> str:
        """Get display title for a chat, using contacts if available."""
        if chat.display_name:
            return chat.display_name
        if chat.participants:
            names = []
            for p in chat.participants[:3]:
                name = self._get_display_name(p.address)
                names.append(name)
            if len(chat.participants) > 3:
                return ", ".join(names) + f" +{len(chat.participants) - 3}"
            return ", ".join(names)
        return chat.chat_identifier

    def _create_chat_row(self, chat: Chat) -> Gtk.ListBoxRow:
        """Create a row for the chat list."""
        row = Gtk.ListBoxRow()
        row.chat = chat  # type: ignore

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Get title using contacts
        title = self._get_chat_title(chat)

        # Avatar
        avatar = Adw.Avatar(size=40, text=title, show_initials=True)
        box.append(avatar)

        # Text content
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        # Name
        name_label = Gtk.Label(label=title, xalign=0)
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name_label.add_css_class("heading")
        text_box.append(name_label)

        # Last message preview
        if chat.last_message:
            last_msg = chat.last_message
            # Skip reactions for preview - show the reaction type instead
            if last_msg.is_reaction:
                tapback = last_msg.tapback_type
                if tapback:
                    reaction_emoji = {
                        TapbackType.LOVE: "❤️",
                        TapbackType.LIKE: "👍",
                        TapbackType.DISLIKE: "👎",
                        TapbackType.LAUGH: "😂",
                        TapbackType.EMPHASIZE: "‼️",
                        TapbackType.QUESTION: "❓",
                    }.get(tapback, "")
                    preview_text = f"Reacted {reaction_emoji}"
                else:
                    preview_text = "Reacted"
                if last_msg.is_from_me:
                    preview_text = f"You: {preview_text}"
            else:
                preview_text = last_msg.text or "(attachment)"
                if last_msg.is_from_me:
                    preview_text = f"You: {preview_text}"
        else:
            preview_text = "No messages"

        preview_label = Gtk.Label(label=preview_text, xalign=0)
        preview_label.set_ellipsize(3)
        preview_label.add_css_class("dim-label")
        preview_label.add_css_class("caption")
        text_box.append(preview_label)

        box.append(text_box)

        row.set_child(box)
        return row

    # Pastel color palette for sender bubbles
    # From https://www.color-hex.com/color-palette/1023412
    SENDER_COLORS = [
        "#ffc7c7",  # Light pink/coral
        "#ffe7c7",  # Light peach
        "#f9ffc7",  # Light yellow
        "#c7ffcb",  # Light green
        "#c7f4ff",  # Light blue
        "#e7c7ff",  # Light purple
        "#ffc7e7",  # Light rose
        "#c7ffe7",  # Light mint
    ]

    # Tapback emoji mapping
    TAPBACK_EMOJI = {
        TapbackType.LOVE: "❤️",
        TapbackType.LIKE: "👍",
        TapbackType.DISLIKE: "👎",
        TapbackType.LAUGH: "😂",
        TapbackType.EMPHASIZE: "‼️",
        TapbackType.QUESTION: "❓",
    }

    def _get_sender_color(self, address: str) -> str:
        """Get a consistent color for a sender based on their address."""
        # Hash the address to get a consistent color index
        color_index = hash(address) % len(self.SENDER_COLORS)
        return self.SENDER_COLORS[color_index]

    def _get_sender_name(self, message: Message) -> str:
        """Get the display name for a message sender."""
        if message.handle:
            return self._get_display_name(message.handle.address)
        return "Unknown"

    def _get_display_name(self, address: str) -> str:
        """Get display name for an address, using contacts if available."""
        # Try exact match first
        if address in self._contacts:
            return self._contacts[address]
        # Try all normalized phone variants
        for variant in self._normalize_phone(address):
            if variant in self._contacts:
                return self._contacts[variant]
        # Try lowercase for emails
        if "@" in address and address.lower() in self._contacts:
            return self._contacts[address.lower()]
        return address

    def _get_message_status(self, message: Message) -> str:
        """Get the delivery status string for a message."""
        if message.is_from_me:
            if message.is_read or message.date_read:
                return "Read"
            elif message.is_delivered or message.date_delivered:
                return "Delivered"
            elif message.is_sent:
                return "Sent"
            elif message.error != 0:
                return "Failed"
            elif message.guid and not message.guid.startswith("temp-"):
                # If we have a real GUID and no error, assume it's sent
                # This handles the case where is_sent isn't set yet
                return "Sent"
            else:
                return "Sending..."
        return ""

    def _create_reaction_badge(
        self, reactions: list[Message], is_from_me: bool
    ) -> Gtk.Widget | None:
        """Create a reaction badge widget like iMessage shows."""
        if not reactions:
            return None

        # Count reactions by type, ignoring "remove" reactions
        reaction_counts: dict[TapbackType, int] = {}
        for reaction in reactions:
            tapback = reaction.tapback_type
            if tapback is None:
                continue
            # Skip remove reactions (3000+)
            if tapback.value >= 3000:
                continue
            reaction_counts[tapback] = reaction_counts.get(tapback, 0) + 1

        if not reaction_counts:
            return None

        # Create the badge container
        badge = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        badge.set_halign(Gtk.Align.END if is_from_me else Gtk.Align.START)
        badge.set_margin_top(-8)  # Overlap with bubble slightly
        badge.set_size_request(-1, 24)  # Ensure minimum height to avoid GTK warning
        if is_from_me:
            badge.set_margin_end(20)
        else:
            badge.set_margin_start(20)

        # Apply badge styling
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .reaction-badge {
                background-color: @card_bg_color;
                border-radius: 12px;
                padding: 2px 6px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
                border: 1px solid alpha(@borders, 0.3);
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        badge.add_css_class("reaction-badge")

        # Add emoji for each reaction type
        for tapback, count in sorted(reaction_counts.items(), key=lambda x: x[0].value):
            emoji = self.TAPBACK_EMOJI.get(tapback, "")
            if emoji:
                label_text = emoji if count == 1 else f"{emoji}{count}"
                emoji_label = Gtk.Label(label=label_text)
                emoji_label.set_margin_start(2)
                emoji_label.set_margin_end(2)
                badge.append(emoji_label)

        return badge

    def _make_text_with_links(self, text: str) -> tuple[str, list[str]]:
        """
        Convert text to Pango markup with clickable links.
        Returns (markup_text, list_of_urls).
        """
        from html import escape

        urls = find_urls(text)
        if not urls:
            return escape(text), []

        # Build the markup text
        result = []
        last_end = 0
        found_urls = []

        for start, end, url in urls:
            # Add text before this URL
            if start > last_end:
                result.append(escape(text[last_end:start]))

            # Add the clickable link
            display_text = text[start:end]
            result.append(f'<a href="{escape(url)}">{escape(display_text)}</a>')
            found_urls.append(url)
            last_end = end

        # Add remaining text
        if last_end < len(text):
            result.append(escape(text[last_end:]))

        return "".join(result), found_urls

    def _create_link_preview_widget(self, preview: LinkPreview) -> Gtk.Widget:
        """Create a link preview card widget."""
        # Container frame
        frame = Gtk.Frame()
        frame.set_margin_top(8)

        # Apply card styling
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .link-preview-card {
                background-color: alpha(@card_bg_color, 0.8);
                border-radius: 12px;
                padding: 8px 12px;
            }
            .link-preview-card:hover {
                background-color: @card_bg_color;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        frame.add_css_class("link-preview-card")

        # Main horizontal box
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.set_margin_start(4)
        main_box.set_margin_end(4)
        main_box.set_margin_top(4)
        main_box.set_margin_bottom(4)

        # Text content box
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        # Site name
        if preview.site_name:
            site_label = Gtk.Label(label=preview.site_name, xalign=0)
            site_label.add_css_class("caption")
            site_label.add_css_class("dim-label")
            site_label.set_ellipsize(3)
            text_box.append(site_label)

        # Title
        if preview.title:
            title_label = Gtk.Label(label=preview.title, xalign=0)
            title_label.add_css_class("heading")
            title_label.set_ellipsize(3)
            title_label.set_max_width_chars(35)
            title_label.set_lines(2)
            title_label.set_wrap(True)
            text_box.append(title_label)

        # Description
        if preview.description:
            desc_label = Gtk.Label(label=preview.description, xalign=0)
            desc_label.add_css_class("caption")
            desc_label.add_css_class("dim-label")
            desc_label.set_ellipsize(3)
            desc_label.set_max_width_chars(40)
            desc_label.set_lines(2)
            desc_label.set_wrap(True)
            text_box.append(desc_label)

        main_box.append(text_box)

        # Image thumbnail (if available and cached)
        if preview.image_url:
            image_box = Gtk.Box()
            image_box.set_size_request(60, 60)
            # Store for async loading
            image_box.image_url = preview.image_url  # type: ignore
            main_box.append(image_box)

        frame.set_child(main_box)

        # Make the whole card clickable
        click_gesture = Gtk.GestureClick()
        url = preview.url

        def on_click(gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
            if n_press == 1:
                Gtk.show_uri(self, url, Gdk.CURRENT_TIME)

        click_gesture.connect("pressed", on_click)
        frame.add_controller(click_gesture)
        frame.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        return frame

    def _create_link_preview_placeholder(self, url: str) -> Gtk.Widget:
        """Create a placeholder for a link preview that will be loaded async."""
        frame = Gtk.Frame()
        frame.set_margin_top(8)

        # Apply styling
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .link-preview-placeholder {
                background-color: alpha(@card_bg_color, 0.5);
                border-radius: 12px;
                padding: 8px 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        frame.add_css_class("link-preview-placeholder")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        spinner = Gtk.Spinner()
        spinner.set_spinning(True)
        spinner.set_size_request(24, 24)
        box.append(spinner)

        label = Gtk.Label(label="Loading preview...")
        label.add_css_class("caption")
        label.add_css_class("dim-label")
        box.append(label)

        frame.set_child(box)

        # Store URL for async loading
        frame.preview_url = url  # type: ignore

        return frame

    def _load_link_preview_async(self, url: str, placeholder: Gtk.Widget, container: Gtk.Box) -> None:
        """Load a link preview asynchronously and replace the placeholder."""
        def fetch_and_update() -> None:
            async def _fetch() -> LinkPreview | None:
                return await fetch_link_preview(url)

            loop = asyncio.new_event_loop()
            try:
                preview = loop.run_until_complete(_fetch())
                if preview and (preview.title or preview.description):
                    def update_widget() -> bool:
                        # Check if placeholder still exists in container
                        if placeholder.get_parent() != container:
                            return False

                        # Create the actual preview widget
                        preview_widget = self._create_link_preview_widget(preview)

                        # Replace placeholder with preview
                        # Get the position of the placeholder
                        children = []
                        child = container.get_first_child()
                        pos = 0
                        found_pos = -1
                        while child:
                            if child == placeholder:
                                found_pos = pos
                            children.append(child)
                            child = child.get_next_sibling()
                            pos += 1

                        if found_pos >= 0:
                            container.remove(placeholder)
                            # Re-add at the same position
                            if found_pos == 0:
                                container.prepend(preview_widget)
                            else:
                                # Insert after the previous widget
                                container.insert_child_after(preview_widget, children[found_pos - 1])

                        return False

                    GLib.idle_add(update_widget)
                else:
                    # Remove placeholder if no preview available
                    def remove_placeholder() -> bool:
                        if placeholder.get_parent() == container:
                            container.remove(placeholder)
                        return False
                    GLib.idle_add(remove_placeholder)
            except Exception as e:
                print(f"Error loading link preview for {url}: {e}")
                # Remove placeholder on error
                def remove_placeholder() -> bool:
                    if placeholder.get_parent() == container:
                        container.remove(placeholder)
                    return False
                GLib.idle_add(remove_placeholder)
            finally:
                loop.close()

        thread = threading.Thread(target=fetch_and_update, daemon=True)
        thread.start()

    def _create_attachment_widget(self, attachment: Attachment) -> Gtk.Widget | None:
        """Create a widget to display an attachment."""
        if not attachment.is_image and not attachment.is_video:
            # For non-image/video attachments, show a file icon with name
            file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            file_box.set_margin_top(4)
            file_box.set_margin_bottom(4)

            icon = Gtk.Image.new_from_icon_name("folder-documents-symbolic")
            icon.set_pixel_size(24)
            file_box.append(icon)

            name = attachment.transfer_name or "attachment"
            size_str = ""
            if attachment.total_bytes > 0:
                if attachment.total_bytes < 1024:
                    size_str = f" ({attachment.total_bytes} B)"
                elif attachment.total_bytes < 1024 * 1024:
                    size_str = f" ({attachment.total_bytes / 1024:.1f} KB)"
                else:
                    size_str = f" ({attachment.total_bytes / (1024 * 1024):.1f} MB)"

            label = Gtk.Label(label=f"{name}{size_str}")
            label.set_ellipsize(3)
            file_box.append(label)

            return file_box

        # For images/videos, create an image widget
        picture_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        picture_box.set_margin_top(4)
        picture_box.set_margin_bottom(4)

        # Check if we have it cached
        if self._cache.has_attachment(attachment.guid):
            path = self._cache.get_attachment_path(attachment.guid)
            picture = Gtk.Picture.new_for_filename(str(path))
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)

            # Limit size
            max_width = 250
            max_height = 300
            if attachment.width and attachment.height:
                aspect = attachment.width / attachment.height
                if attachment.width > max_width:
                    display_width = max_width
                    display_height = int(max_width / aspect)
                else:
                    display_width = attachment.width
                    display_height = attachment.height
                if display_height > max_height:
                    display_height = max_height
                    display_width = int(max_height * aspect)
            else:
                display_width = max_width
                display_height = max_height

            picture.set_size_request(display_width, display_height)

            # Make image clickable for full-size preview
            click_gesture = Gtk.GestureClick()
            image_path = str(path)
            image_name = attachment.transfer_name or "Image"

            def on_image_click(
                gesture: Gtk.GestureClick,
                n_press: int,
                x: float,
                y: float,
                path: str = image_path,
                name: str = image_name,
            ) -> None:
                if n_press == 1:
                    self._show_image_preview(path, name)

            click_gesture.connect("pressed", on_image_click)
            picture.add_controller(click_gesture)
            picture.set_cursor(Gdk.Cursor.new_from_name("pointer"))

            picture_box.append(picture)

            # Add video overlay icon if it's a video
            if attachment.is_video:
                overlay = Gtk.Overlay()
                overlay.set_child(picture)
                play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                play_icon.set_pixel_size(48)
                play_icon.set_opacity(0.8)
                play_icon.set_halign(Gtk.Align.CENTER)
                play_icon.set_valign(Gtk.Align.CENTER)
                overlay.add_overlay(play_icon)
                picture_box.remove(picture)
                picture_box.append(overlay)
        else:
            # Show placeholder with loading spinner
            placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            placeholder.set_size_request(200, 150)
            placeholder.set_valign(Gtk.Align.CENTER)
            placeholder.set_halign(Gtk.Align.CENTER)

            # Apply placeholder styling
            css = Gtk.CssProvider()
            css.load_from_data(b"""
                .attachment-placeholder {
                    background-color: rgba(128, 128, 128, 0.2);
                    border-radius: 12px;
                    padding: 16px;
                }
            """)
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            placeholder.add_css_class("attachment-placeholder")

            spinner = Gtk.Spinner()
            spinner.set_spinning(True)
            spinner.set_size_request(32, 32)
            spinner.set_halign(Gtk.Align.CENTER)
            placeholder.append(spinner)

            loading_label = Gtk.Label(label="Loading...")
            loading_label.add_css_class("dim-label")
            loading_label.add_css_class("caption")
            placeholder.append(loading_label)

            picture_box.append(placeholder)

            # Store reference for later update
            picture_box.attachment_guid = attachment.guid  # type: ignore
            picture_box.placeholder = placeholder  # type: ignore

        return picture_box

    def _load_attachment_async(
        self,
        attachment: Attachment,
        widget: Gtk.Box,
    ) -> None:
        """Load an attachment asynchronously and update the widget."""
        def fetch_and_update() -> None:
            async def _fetch() -> bytes | None:
                if not self.app.config.is_configured:
                    return None

                client = BlueBubblesClient(
                    self.app.config.server_url,  # type: ignore
                    self.app.config.password,  # type: ignore
                    timeout=60.0,  # Longer timeout for large attachments
                )
                try:
                    await client.connect()
                    return await client.get_attachment(attachment.guid)
                except Exception as e:
                    print(f"Error fetching attachment {attachment.guid}: {e}")
                    return None
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            try:
                data = loop.run_until_complete(_fetch())
                if data:
                    # Save to cache
                    path = self._cache.save_attachment(attachment.guid, data)

                    # Update UI
                    def update_widget() -> bool:
                        # Check if widget still exists and has our placeholder
                        if not hasattr(widget, 'placeholder'):
                            return False

                        # Remove placeholder
                        widget.remove(widget.placeholder)  # type: ignore

                        # Add the image
                        picture = Gtk.Picture.new_for_filename(str(path))
                        picture.set_can_shrink(True)
                        picture.set_content_fit(Gtk.ContentFit.CONTAIN)

                        max_width = 250
                        max_height = 300
                        if attachment.width and attachment.height:
                            aspect = attachment.width / attachment.height
                            if attachment.width > max_width:
                                display_width = max_width
                                display_height = int(max_width / aspect)
                            else:
                                display_width = attachment.width
                                display_height = attachment.height
                            if display_height > max_height:
                                display_height = max_height
                                display_width = int(max_height * aspect)
                        else:
                            display_width = max_width
                            display_height = max_height

                        picture.set_size_request(display_width, display_height)

                        if attachment.is_video:
                            overlay = Gtk.Overlay()
                            overlay.set_child(picture)
                            play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                            play_icon.set_pixel_size(48)
                            play_icon.set_opacity(0.8)
                            play_icon.set_halign(Gtk.Align.CENTER)
                            play_icon.set_valign(Gtk.Align.CENTER)
                            overlay.add_overlay(play_icon)
                            widget.append(overlay)
                        else:
                            widget.append(picture)

                        return False

                    GLib.idle_add(update_widget)
            except Exception as e:
                print(f"Error loading attachment: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=fetch_and_update, daemon=True)
        thread.start()

    def _create_message_bubble(
        self, message: Message, reactions: list[Message] | None = None
    ) -> Gtk.Box:
        """Create a message bubble widget with sender info, status, and reactions."""
        # Container for bubble + reactions
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.set_margin_top(2)
        container.set_margin_bottom(2)

        outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Determine if this is a group chat
        is_group = self._selected_chat and self._selected_chat.is_group

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        if message.is_from_me:
            # Sent message - blue/right aligned
            bubble.set_margin_start(80)
            bubble.set_margin_end(12)
            bubble.set_halign(Gtk.Align.END)
            outer_box.set_halign(Gtk.Align.END)

            # Apply iMessage blue style via CSS
            css_provider = Gtk.CssProvider()
            css_provider.load_from_data(b"""
                .message-bubble-sent {
                    background-color: #007AFF;
                    color: white;
                    border-radius: 18px;
                    padding: 10px 14px;
                }
                .message-bubble-sent label {
                    color: white;
                }
                .status-label-sent {
                    color: rgba(255, 255, 255, 0.7);
                }
            """)
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            bubble.add_css_class("message-bubble-sent")
        else:
            # Received message - colored based on sender
            bubble.set_margin_start(12)
            bubble.set_margin_end(80)
            bubble.set_halign(Gtk.Align.START)
            outer_box.set_halign(Gtk.Align.START)

            # Get sender color
            sender_address = self._get_sender_name(message)
            sender_color = self._get_sender_color(sender_address)

            # Show sender name in group chats
            if is_group:
                sender_label = Gtk.Label(label=sender_address, xalign=0)
                sender_label.add_css_class("caption")
                sender_label.set_margin_bottom(2)
                # Apply sender color to name
                name_css = Gtk.CssProvider()
                name_css.load_from_data(f"""
                    .sender-name {{
                        color: darker({sender_color});
                        font-weight: 600;
                    }}
                """.encode())
                Gtk.StyleContext.add_provider_for_display(
                    self.get_display(),
                    name_css,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                sender_label.add_css_class("sender-name")
                bubble.append(sender_label)

            # Apply colored bubble style
            bubble_css = Gtk.CssProvider()
            bubble_css.load_from_data(f"""
                .message-bubble-received {{
                    background-color: {sender_color};
                    color: #333333;
                    border-radius: 18px;
                    padding: 10px 14px;
                }}
            """.encode())
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                bubble_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            bubble.add_css_class("message-bubble-received")

        # Message text with clickable links
        link_preview_urls: list[str] = []
        if message.text:
            markup_text, found_urls = self._make_text_with_links(message.text)
            link_preview_urls = found_urls

            text_label = Gtk.Label(xalign=0)
            text_label.set_wrap(True)
            text_label.set_max_width_chars(45)
            text_label.set_selectable(True)

            if found_urls:
                # Use markup for clickable links
                text_label.set_markup(markup_text)
                # Connect to activate-link to handle clicks
                def on_link_activate(label: Gtk.Label, uri: str) -> bool:
                    Gtk.show_uri(self, uri, Gdk.CURRENT_TIME)
                    return True  # Stop propagation
                text_label.connect("activate-link", on_link_activate)
            else:
                text_label.set_text(message.text)

            bubble.append(text_label)

        # Link previews (show only for the first URL to avoid clutter)
        link_preview_placeholders: list[tuple[str, Gtk.Widget]] = []
        if link_preview_urls:
            # Only show preview for first URL
            url = link_preview_urls[0]
            placeholder = self._create_link_preview_placeholder(url)
            bubble.append(placeholder)
            link_preview_placeholders.append((url, placeholder))

        # Attachments
        attachment_widgets: list[tuple[Attachment, Gtk.Box]] = []
        if message.attachments:  # Check array directly since has_attachments can be False even with attachments
            for attachment in message.attachments:
                if attachment.hide_attachment:
                    continue
                widget = self._create_attachment_widget(attachment)
                if widget:
                    bubble.append(widget)
                    # Track widgets that need async loading
                    if isinstance(widget, Gtk.Box) and hasattr(widget, 'placeholder'):
                        attachment_widgets.append((attachment, widget))

        # Status row: sender (for received) + timestamp + delivery status
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_size_request(-1, 24)  # Ensure minimum height to avoid GTK warning

        # Timestamp
        time_str = message.date_created_dt.strftime("%I:%M %p")
        time_label = Gtk.Label(label=time_str)
        time_label.add_css_class("caption")

        if message.is_from_me:
            time_label.add_css_class("status-label-sent")
            status_box.set_halign(Gtk.Align.END)

            # Delivery status
            status = self._get_message_status(message)
            if status:
                status_label = Gtk.Label(label=f"· {status}")
                status_label.add_css_class("caption")
                status_label.add_css_class("status-label-sent")
                status_box.append(time_label)
                status_box.append(status_label)
            else:
                status_box.append(time_label)
        else:
            status_box.set_halign(Gtk.Align.START)
            # Show sender name in status for non-group chats too
            if not is_group:
                sender_name = self._get_sender_name(message)
                sender_status = Gtk.Label(label=sender_name)
                sender_status.add_css_class("caption")
                sender_status.add_css_class("dim-label")
                status_box.append(sender_status)
                sep = Gtk.Label(label="·")
                sep.add_css_class("caption")
                sep.add_css_class("dim-label")
                status_box.append(sep)
            time_label.add_css_class("dim-label")
            status_box.append(time_label)

        bubble.append(status_box)

        outer_box.append(bubble)
        outer_box.set_hexpand(True)

        container.append(outer_box)

        # Add reaction badge if there are reactions
        if reactions:
            reaction_badge = self._create_reaction_badge(reactions, message.is_from_me)
            if reaction_badge:
                container.append(reaction_badge)

        # Store pending attachments on container instead of outer_box
        if attachment_widgets:
            container._pending_attachments = attachment_widgets  # type: ignore

        # Store pending link previews
        if link_preview_placeholders:
            container._pending_link_previews = link_preview_placeholders  # type: ignore
            container._link_preview_bubble = bubble  # type: ignore

        # Store message reference for context menu
        container._message = message  # type: ignore
        container._bubble = bubble  # type: ignore

        # Add right-click context menu
        self._add_message_context_menu(container, bubble, message)

        return container

    def _add_message_context_menu(
        self, container: Gtk.Box, bubble: Gtk.Box, message: Message
    ) -> None:
        """Add right-click context menu to a message bubble."""
        # Create popover for context menu
        popover = Gtk.Popover()
        popover.set_parent(bubble)
        popover.set_has_arrow(True)
        popover.set_position(Gtk.PositionType.TOP)

        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        menu_box.set_margin_top(8)
        menu_box.set_margin_bottom(8)
        menu_box.set_margin_start(8)
        menu_box.set_margin_end(8)

        # Reaction row
        reaction_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        reaction_box.set_halign(Gtk.Align.CENTER)
        reaction_box.set_size_request(-1, 32)

        reactions = [
            (TapbackType.LOVE, "❤️"),
            (TapbackType.LIKE, "👍"),
            (TapbackType.DISLIKE, "👎"),
            (TapbackType.LAUGH, "😂"),
            (TapbackType.EMPHASIZE, "‼️"),
            (TapbackType.QUESTION, "❓"),
        ]

        for tapback_type, emoji in reactions:
            btn = Gtk.Button(label=emoji)
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.set_tooltip_text(tapback_type.name.capitalize())

            def on_reaction_click(
                _btn: Gtk.Button,
                reaction_type: TapbackType = tapback_type,
                msg: Message = message,
            ) -> None:
                popover.popdown()
                self._send_reaction_async(msg, reaction_type)

            btn.connect("clicked", on_reaction_click)
            reaction_box.append(btn)

        menu_box.append(reaction_box)

        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(4)
        separator.set_margin_bottom(4)
        menu_box.append(separator)

        # Action buttons
        actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        # Copy button (if message has text)
        if message.text:
            copy_btn = Gtk.Button()
            copy_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            copy_icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic")
            copy_btn_box.append(copy_icon)
            copy_btn_box.append(Gtk.Label(label="Copy Text"))
            copy_btn.set_child(copy_btn_box)
            copy_btn.add_css_class("flat")

            def on_copy_click(_btn: Gtk.Button, txt: str = message.text) -> None:
                popover.popdown()
                clipboard = self.get_clipboard()
                clipboard.set(txt)

            copy_btn.connect("clicked", on_copy_click)
            actions_box.append(copy_btn)

        # Edit button (only for own messages with text, not attachments-only)
        if message.is_from_me and message.text:
            edit_btn = Gtk.Button()
            edit_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            edit_icon = Gtk.Image.new_from_icon_name("document-edit-symbolic")
            edit_btn_box.append(edit_icon)
            edit_btn_box.append(Gtk.Label(label="Edit Message"))
            edit_btn.set_child(edit_btn_box)
            edit_btn.add_css_class("flat")

            def on_edit_click(
                _btn: Gtk.Button, msg: Message = message, bbl: Gtk.Box = bubble
            ) -> None:
                popover.popdown()
                self._start_inline_edit(container, bbl, msg)

            edit_btn.connect("clicked", on_edit_click)
            actions_box.append(edit_btn)

        menu_box.append(actions_box)
        popover.set_child(menu_box)

        # Right-click gesture
        right_click = Gtk.GestureClick()
        right_click.set_button(3)  # Right mouse button

        def on_right_click(
            gesture: Gtk.GestureClick, n_press: int, x: float, y: float
        ) -> None:
            if n_press == 1:
                # Position popover at click location
                rect = Gdk.Rectangle()
                rect.x = int(x)
                rect.y = int(y)
                rect.width = 1
                rect.height = 1
                popover.set_pointing_to(rect)
                popover.popup()

        right_click.connect("pressed", on_right_click)
        bubble.add_controller(right_click)

        # Also support long-press for touch devices
        long_press = Gtk.GestureLongPress()
        long_press.set_delay_factor(1.0)

        def on_long_press(
            gesture: Gtk.GestureLongPress, x: float, y: float
        ) -> None:
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            popover.set_pointing_to(rect)
            popover.popup()

        long_press.connect("pressed", on_long_press)
        bubble.add_controller(long_press)

    def _send_reaction_async(self, message: Message, reaction_type: TapbackType) -> None:
        """Send a reaction to a message asynchronously."""
        if not self._selected_chat:
            return

        chat_guid = self._selected_chat.guid
        message_guid = message.guid
        # API expects lowercase string like "love", "like", etc.
        reaction_name = reaction_type.name.lower()

        def send_reaction() -> None:
            async def _send() -> Message | None:
                client = self.app.get_client()
                if client is None:
                    return None
                async with client:
                    return await client.send_reaction(
                        chat_guid, message_guid, reaction_name
                    )

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_send())
                if result:
                    print(f"Sent reaction {reaction_name} to message")
            except Exception as exc:
                error_msg = str(exc)
                print(f"Error sending reaction: {error_msg}")

                def show_error(msg: str = error_msg) -> bool:
                    print(f"Reaction error: {msg}")
                    return False

                GLib.idle_add(show_error)
            finally:
                loop.close()

        thread = threading.Thread(target=send_reaction, daemon=True)
        thread.start()

    def _start_inline_edit(
        self, container: Gtk.Box, bubble: Gtk.Box, message: Message
    ) -> None:
        """Start inline editing of a message."""
        if not message.text:
            return

        # Find the text label in the bubble and replace it with an entry
        # The text label should be one of the first children
        text_label: Gtk.Label | None = None
        text_label_position = -1

        child = bubble.get_first_child()
        position = 0
        children = []
        while child:
            children.append(child)
            if isinstance(child, Gtk.Label) and child.get_text() == message.text:
                text_label = child
                text_label_position = position
            child = child.get_next_sibling()
            position += 1

        if text_label is None:
            # Try to find label with markup (links)
            for i, c in enumerate(children):
                if isinstance(c, Gtk.Label):
                    # Check if label has selectable text matching our message
                    label_text = c.get_text()
                    if label_text and message.text and label_text.strip():
                        text_label = c
                        text_label_position = i
                        break

        if text_label is None:
            print("Could not find text label to edit")
            return

        # Hide the original label
        text_label.set_visible(False)

        # Create edit entry
        edit_entry = Gtk.Entry()
        edit_entry.set_text(message.text)
        edit_entry.add_css_class("edit-entry")

        # Apply styling
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .edit-entry {
                background-color: rgba(255, 255, 255, 0.9);
                color: #333;
                border-radius: 8px;
                padding: 4px 8px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Insert entry after the label
        bubble.insert_child_after(edit_entry, text_label)

        # Cancel button
        cancel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        cancel_box.set_margin_top(4)

        hint_label = Gtk.Label(label="Enter to save, Escape to cancel")
        hint_label.add_css_class("caption")
        hint_label.add_css_class("dim-label")
        cancel_box.append(hint_label)

        bubble.insert_child_after(cancel_box, edit_entry)

        def cancel_edit() -> None:
            """Cancel editing and restore original state."""
            edit_entry.set_visible(False)
            bubble.remove(edit_entry)
            cancel_box.set_visible(False)
            bubble.remove(cancel_box)
            text_label.set_visible(True)

        def submit_edit() -> None:
            """Submit the edit."""
            new_text = edit_entry.get_text().strip()
            if not new_text or new_text == message.text:
                cancel_edit()
                return

            # Disable entry while sending
            edit_entry.set_sensitive(False)
            hint_label.set_text("Sending edit...")

            def do_edit() -> None:
                async def _edit() -> Message | None:
                    client = self.app.get_client()
                    if client is None:
                        return None
                    async with client:
                        return await client.edit_message(message.guid, new_text)

                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(_edit())

                    def update_ui() -> bool:
                        if result:
                            # Update the message text locally
                            text_label.set_text(new_text)
                            # Update in our messages list
                            for i, m in enumerate(self._messages):
                                if m.guid == message.guid:
                                    # Create updated message
                                    msg_data = m.model_dump(by_alias=True)
                                    msg_data["text"] = new_text
                                    self._messages[i] = Message(**msg_data)
                                    break
                            cancel_edit()
                        else:
                            hint_label.set_text("Edit failed")
                            edit_entry.set_sensitive(True)
                        return False

                    GLib.idle_add(update_ui)
                except Exception as e:
                    print(f"Error editing message: {e}")

                    def show_error() -> bool:
                        hint_label.set_text(f"Error: {e}")
                        edit_entry.set_sensitive(True)
                        return False

                    GLib.idle_add(show_error)
                finally:
                    loop.close()

            thread = threading.Thread(target=do_edit, daemon=True)
            thread.start()

        def on_key_pressed(
            controller: Gtk.EventControllerKey,
            keyval: int,
            keycode: int,
            state: Gdk.ModifierType,
        ) -> bool:
            if keyval == Gdk.KEY_Escape:
                cancel_edit()
                return True
            elif keyval == Gdk.KEY_Return or keyval == Gdk.KEY_KP_Enter:
                submit_edit()
                return True
            return False

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", on_key_pressed)
        edit_entry.add_controller(key_controller)

        # Focus the entry
        edit_entry.grab_focus()
        edit_entry.select_region(0, -1)  # Select all text

    def _load_chats(self) -> None:
        """Load chats from cache first, then sync with server."""
        self._chats = []
        self._chats_by_guid = {}
        self._loading_chats = True

        def show_status(message: str, spinning: bool = True) -> bool:
            self._status_bar.set_visible(True)
            self._status_spinner.set_spinning(spinning)
            self._status_label.set_text(message)
            return False

        def hide_status() -> bool:
            self._status_bar.set_visible(False)
            self._status_spinner.set_spinning(False)
            return False

        def add_chats_to_ui(new_chats: list[Chat], from_cache: bool = False) -> bool:
            for chat in new_chats:
                if chat.guid in self._chats_by_guid:
                    # Update existing chat - find and update the row
                    self._chats_by_guid[chat.guid] = chat
                    # Update in list too
                    for i, c in enumerate(self._chats):
                        if c.guid == chat.guid:
                            self._chats[i] = chat
                            break
                else:
                    # New chat
                    self._chats.append(chat)
                    self._chats_by_guid[chat.guid] = chat
                    row = self._create_chat_row(chat)
                    self._chat_list.append(row)
            return False

        def load_and_sync() -> None:
            # Step 1: Load from cache immediately
            cached_chats = self._cache.get_all_chats()
            if cached_chats:
                GLib.idle_add(show_status, f"Loaded {len(cached_chats)} from cache, syncing...")
                GLib.idle_add(add_chats_to_ui, cached_chats, True)

            # Step 2: Fetch from server
            async def _sync() -> None:
                from ..api import BlueBubblesClient

                if not self.app.config.is_configured:
                    return

                # Create a dedicated client for syncing (with longer timeout)
                client = BlueBubblesClient(
                    self.app.config.server_url,  # type: ignore
                    self.app.config.password,  # type: ignore
                    timeout=60.0,  # Longer timeout for sync
                )

                batch_size = 50
                offset = 0
                total_synced = 0
                new_chats_batch: list[Chat] = []

                try:
                    await client.connect()

                    while True:
                        cached_count = len(self._chats)
                        GLib.idle_add(
                            show_status,
                            f"Syncing... ({total_synced} fetched, {cached_count} total)"
                        )

                        try:
                            batch = await client.get_chats(
                                limit=batch_size,
                                offset=offset,
                                with_last_message=True,
                                with_participants=True,
                            )
                        except Exception as e:
                            print(f"Error syncing chats at offset {offset}: {e}")
                            break

                        if not batch:
                            break

                        total_synced += len(batch)

                        # Save to cache
                        self._cache.save_chats(batch)

                        # Add new chats to UI
                        new_chats = [c for c in batch if c.guid not in self._chats_by_guid]
                        if new_chats:
                            new_chats_batch.extend(new_chats)
                            GLib.idle_add(add_chats_to_ui, new_chats, False)

                        if len(batch) < batch_size:
                            break

                        offset += batch_size
                finally:
                    await client.close()

                final_count = len(self._chats_by_guid)
                new_count = len(new_chats_batch)
                if new_count > 0:
                    GLib.idle_add(
                        show_status,
                        f"Synced! {new_count} new conversations ({final_count} total)",
                        False
                    )
                else:
                    GLib.idle_add(
                        show_status,
                        f"Up to date ({final_count} conversations)",
                        False
                    )
                GLib.timeout_add(2000, hide_status)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_sync())
            except Exception as e:
                print(f"Error syncing chats: {e}")
                GLib.idle_add(show_status, f"Sync error: {e}", False)
            finally:
                loop.close()
                self._loading_chats = False

        thread = threading.Thread(target=load_and_sync, daemon=True)
        thread.start()

    def _connect_socket(self) -> None:
        """Connect to BlueBubbles Socket.IO for real-time updates."""
        if not self.app.config.is_configured:
            return

        def run_socket() -> None:
            async def _connect() -> None:
                self._socket = BlueBubblesSocket(
                    self.app.config.server_url,  # type: ignore
                    self.app.config.password,  # type: ignore
                )

                # Register callbacks
                self._socket.on_new_message(self._on_socket_new_message)
                self._socket.on_message_updated(self._on_socket_message_updated)
                self._socket.on_connected(self._on_socket_connected)
                self._socket.on_disconnected(self._on_socket_disconnected)

                try:
                    await self._socket.connect()
                    await self._socket.wait()
                except Exception as e:
                    print(f"Socket connection error: {e}")

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_connect())
            except Exception as e:
                print(f"Socket thread error: {e}")
            finally:
                loop.close()

        self._socket_thread = threading.Thread(target=run_socket, daemon=True)
        self._socket_thread.start()

    def _on_socket_connected(self) -> None:
        """Handle socket connection established."""
        def update_ui() -> bool:
            print("Socket.IO connected - real-time updates enabled")
            return False
        GLib.idle_add(update_ui)

    def _on_socket_disconnected(self) -> None:
        """Handle socket disconnection."""
        def update_ui() -> bool:
            print("Socket.IO disconnected")
            return False
        GLib.idle_add(update_ui)

    def _on_socket_new_message(self, message: Message, chat_guid: str) -> None:
        """Handle new message from socket."""
        def update_ui() -> bool:
            # Save to cache
            self._cache.save_messages(chat_guid, [message])

            # Check if this is a reaction message
            if message.is_reaction:
                # Handle reaction - update the target message's reaction badge
                self._handle_new_reaction(message, chat_guid)
                return False

            # Send notification if appropriate
            if self._should_notify(message, chat_guid):
                self._send_notification(message, chat_guid)

            # Update chat list if this is a new message for an existing chat
            if chat_guid in self._chats_by_guid:
                chat = self._chats_by_guid[chat_guid]
                # Update last message
                chat_data = chat.model_dump(by_alias=True)
                chat_data["lastMessage"] = message.model_dump(by_alias=True)
                updated_chat = Chat(**chat_data)
                self._chats_by_guid[chat_guid] = updated_chat

                # Remove from current position and add to top
                self._chats = [c for c in self._chats if c.guid != chat_guid]
                self._chats.insert(0, updated_chat)

                # Rebuild the chat list UI to reflect new order
                self._rebuild_chat_list_preserving_selection()

            # If this chat is currently selected, add the message to the view
            if self._selected_chat and self._selected_chat.guid == chat_guid:
                # Check if message already exists
                if not any(m.guid == message.guid for m in self._messages):
                    self._messages.insert(0, message)
                    bubble = self._create_message_bubble(message, None)
                    self._message_list.append(bubble)

                    # Load any pending attachments
                    if hasattr(bubble, '_pending_attachments'):
                        for attachment, widget in bubble._pending_attachments:  # type: ignore
                            self._load_attachment_async(attachment, widget)

                    # Load any pending link previews
                    if hasattr(bubble, '_pending_link_previews'):
                        for url, placeholder in bubble._pending_link_previews:  # type: ignore
                            self._load_link_preview_async(url, placeholder, bubble._link_preview_bubble)  # type: ignore

                    # Scroll to show the new message
                    self._scroll_to_bottom()

            return False

        GLib.idle_add(update_ui)

    def _handle_new_reaction(self, reaction_message: Message, chat_guid: str) -> None:
        """Handle a new reaction message by updating the target message's badge."""
        if not self._selected_chat or self._selected_chat.guid != chat_guid:
            return

        # Add reaction to our messages list
        if not any(m.guid == reaction_message.guid for m in self._messages):
            self._messages.insert(0, reaction_message)

        # Find the target message GUID
        target_guid = reaction_message.associated_message_guid
        if not target_guid:
            return

        # Extract UUID from "p:X/UUID" format if present
        if "/" in target_guid:
            target_guid = target_guid.split("/")[-1]

        # Find the target message in our list
        target_message: Message | None = None
        for msg in self._messages:
            if msg.guid == target_guid:
                target_message = msg
                break

        if not target_message:
            return

        # Collect all reactions for this message
        reactions: list[Message] = []
        for msg in self._messages:
            if msg.is_reaction and msg.associated_message_guid:
                msg_target = msg.associated_message_guid
                if "/" in msg_target:
                    msg_target = msg_target.split("/")[-1]
                if msg_target == target_guid:
                    reactions.append(msg)

        # Find the bubble container for the target message and rebuild it
        # We need to find the widget in _message_list that corresponds to the target message
        child = self._message_list.get_first_child()
        prev_sibling: Gtk.Widget | None = None
        while child:
            if hasattr(child, '_message') and child._message.guid == target_guid:  # type: ignore
                # Found the bubble container - rebuild it with updated reactions
                self._message_list.remove(child)

                # Create new bubble with reactions
                new_bubble = self._create_message_bubble(target_message, reactions)

                # Insert at the same position (after prev_sibling, or at start if none)
                if prev_sibling:
                    self._message_list.insert_child_after(new_bubble, prev_sibling)
                else:
                    self._message_list.prepend(new_bubble)

                # Load any pending attachments
                if hasattr(new_bubble, '_pending_attachments'):
                    for attachment, widget in new_bubble._pending_attachments:  # type: ignore
                        self._load_attachment_async(attachment, widget)

                break
            prev_sibling = child
            child = child.get_next_sibling()

    def _on_socket_message_updated(self, message: Message) -> None:
        """Handle message update from socket (delivered, read, etc)."""
        def update_ui() -> bool:
            # Update message in current view if it exists
            if self._selected_chat:
                for i, m in enumerate(self._messages):
                    if m.guid == message.guid:
                        self._messages[i] = message
                        # Rebuild the message list to reflect the update
                        self._update_message_list()
                        break
            return False

        GLib.idle_add(update_ui)

    def _normalize_phone(self, phone: str) -> list[str]:
        """
        Normalize a phone number for comparison (remove formatting).
        Returns multiple variants to handle country code differences.
        """
        import re
        # Remove all non-digit characters
        digits_only = re.sub(r"[^\d]", "", phone)

        variants = []

        # Add the digits-only version
        if digits_only:
            variants.append(digits_only)

            # If it starts with country code 1 (US/Canada), also try without it
            if digits_only.startswith("1") and len(digits_only) == 11:
                variants.append(digits_only[1:])  # Without country code

            # If it's 10 digits, also try with +1 prefix
            if len(digits_only) == 10:
                variants.append("1" + digits_only)  # With country code
                variants.append("+1" + digits_only)  # With + prefix

            # Also add +digits version
            variants.append("+" + digits_only)

        return variants

    def _load_contacts(self) -> None:
        """Load contacts from cache first, then sync from server."""
        # Step 1: Load from cache immediately
        cached_contacts = self._cache.get_all_contacts()
        if cached_contacts:
            self._contacts = cached_contacts
            print(f"Loaded {len(cached_contacts)} contacts from cache")
            if self._chats:
                self._update_chat_list()

        def fetch_contacts() -> None:
            async def _fetch() -> None:
                if not self.app.config.is_configured:
                    return

                client = BlueBubblesClient(
                    self.app.config.server_url,  # type: ignore
                    self.app.config.password,  # type: ignore
                    timeout=30.0,
                )
                try:
                    await client.connect()
                    contacts = await client.get_contacts()

                    # Build address -> name mapping
                    contact_map: dict[str, str] = {}
                    for contact in contacts:
                        name = contact.name
                        if not name:
                            continue
                        # Map all phone numbers and emails to this contact's name
                        for phone in contact.phones:
                            addr = phone.get("address", "")
                            if addr:
                                # Store original and all normalized variants
                                contact_map[addr] = name
                                for variant in self._normalize_phone(addr):
                                    contact_map[variant] = name
                        for email in contact.emails:
                            addr = email.get("address", "")
                            if addr:
                                contact_map[addr.lower()] = name

                    def update_contacts() -> bool:
                        self._contacts = contact_map
                        print(f"Loaded {len(contact_map)} contact mappings from {len(contacts)} contacts")
                        # Debug: show first few mappings and check specific numbers
                        for i, (addr, name) in enumerate(list(contact_map.items())[:5]):
                            print(f"  Contact: {addr} -> {name}")
                        # Check for specific numbers user asked about
                        for test_num in ["+14157309533", "+13397880769"]:
                            found = False
                            if test_num in contact_map:
                                print(f"  FOUND: {test_num} -> {contact_map[test_num]}")
                                found = True
                            else:
                                for variant in self._normalize_phone(test_num):
                                    if variant in contact_map:
                                        print(f"  FOUND ({variant}): {test_num} -> {contact_map[variant]}")
                                        found = True
                                        break
                            if not found:
                                print(f"  NOT FOUND: {test_num}")
                        # Save to cache
                        self._cache.save_contacts(contact_map)
                        # Refresh chat list to show new names
                        if self._chats:
                            self._update_chat_list()
                        return False

                    GLib.idle_add(update_contacts)
                except Exception as e:
                    print(f"Error loading contacts: {e}")
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_fetch())
            except Exception as e:
                print(f"Contact fetch error: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=fetch_contacts, daemon=True)
        thread.start()

    def _scroll_to_bottom(self) -> None:
        """Scroll the message list to the bottom."""
        def do_scroll() -> bool:
            if self._message_scroll:
                adj = self._message_scroll.get_vadjustment()
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return False
        # Delay slightly to let the UI render
        GLib.timeout_add(100, do_scroll)

    def _show_image_preview(self, image_path: str, title: str = "Image") -> None:
        """Show a fullscreen image preview dialog."""
        dialog = Adw.Dialog()
        dialog.set_title(title)
        dialog.set_content_width(800)
        dialog.set_content_height(600)

        # Main box
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar with close button
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        box.append(header)

        # Scrolled window for the image
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Full-size image
        picture = Gtk.Picture.new_for_filename(image_path)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        scrolled.set_child(picture)

        box.append(scrolled)

        dialog.set_child(box)
        dialog.present(self)

    def _update_chat_list(self) -> None:
        """Update the chat list UI."""
        # Clear existing rows
        while True:
            row = self._chat_list.get_row_at_index(0)
            if row is None:
                break
            self._chat_list.remove(row)

        # Add chat rows
        for chat in self._chats:
            row = self._create_chat_row(chat)
            self._chat_list.append(row)

    def _rebuild_chat_list_preserving_selection(self) -> None:
        """Rebuild the chat list UI while preserving the current selection."""
        # Remember the currently selected chat
        selected_guid = self._selected_chat.guid if self._selected_chat else None

        # Clear existing rows
        while True:
            row = self._chat_list.get_row_at_index(0)
            if row is None:
                break
            self._chat_list.remove(row)

        # Add chat rows in new order
        selected_row: Gtk.ListBoxRow | None = None
        for chat in self._chats:
            row = self._create_chat_row(chat)
            self._chat_list.append(row)
            # Track the row for the selected chat
            if selected_guid and chat.guid == selected_guid:
                selected_row = row

        # Restore selection without triggering the handler
        if selected_row:
            self._chat_list.select_row(selected_row)

    def _on_chat_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Handle chat selection."""
        if row is None:
            self._selected_chat = None
            return

        self._selected_chat = row.chat  # type: ignore

        # Update header with colored participant names
        self._update_chat_header()

        # Show compose box
        self._compose_box.set_visible(True)

        # Load messages
        self._load_messages()

        # On mobile, show the content page
        self._split_view.set_show_content(True)

    def _update_chat_header(self) -> None:
        """Update the chat header with colored participant names."""
        if self._selected_chat is None:
            self._content_title.set_title("Select a conversation")
            self._content_title.set_subtitle("")
            return

        chat = self._selected_chat

        if chat.display_name:
            # Use display name as title
            self._content_title.set_title(chat.display_name)

            # Show colored participant names as subtitle for groups
            if chat.is_group and chat.participants:
                subtitle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                subtitle_box.set_halign(Gtk.Align.CENTER)
                subtitle_box.set_size_request(-1, 24)  # Ensure minimum height

                for i, participant in enumerate(chat.participants[:5]):
                    if i > 0:
                        sep = Gtk.Label(label=", ")
                        sep.add_css_class("caption")
                        sep.add_css_class("dim-label")
                        subtitle_box.append(sep)

                    display_name = self._get_display_name(participant.address)
                    color = self._get_sender_color(participant.address)
                    name_label = Gtk.Label(label=display_name)
                    name_label.add_css_class("caption")

                    # Apply color
                    css = Gtk.CssProvider()
                    css.load_from_data(f"""
                        .participant-{abs(hash(participant.address)) % 10000} {{
                            color: shade({color}, 0.6);
                            font-weight: 500;
                        }}
                    """.encode())
                    Gtk.StyleContext.add_provider_for_display(
                        self.get_display(),
                        css,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )
                    name_label.add_css_class(f"participant-{abs(hash(participant.address)) % 10000}")
                    subtitle_box.append(name_label)

                if len(chat.participants) > 5:
                    more = Gtk.Label(label=f" +{len(chat.participants) - 5} more")
                    more.add_css_class("caption")
                    more.add_css_class("dim-label")
                    subtitle_box.append(more)

                # Replace the title widget temporarily
                self._content_header.set_title_widget(None)
                title_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                title_container.set_valign(Gtk.Align.CENTER)

                title_label = Gtk.Label(label=chat.display_name)
                title_label.add_css_class("title")
                title_container.append(title_label)
                title_container.append(subtitle_box)

                self._content_header.set_title_widget(title_container)
            else:
                self._content_title.set_subtitle("")
                self._content_header.set_title_widget(self._content_title)
        elif chat.participants:
            # No display name - show colored participant names as title
            if len(chat.participants) == 1:
                # Single participant - just show their name
                participant = chat.participants[0]
                display_name = self._get_display_name(participant.address)
                color = self._get_sender_color(participant.address)

                title_label = Gtk.Label(label=display_name)
                title_label.add_css_class("title")

                css = Gtk.CssProvider()
                css.load_from_data(f"""
                    .single-participant {{
                        color: shade({color}, 0.6);
                    }}
                """.encode())
                Gtk.StyleContext.add_provider_for_display(
                    self.get_display(),
                    css,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                title_label.add_css_class("single-participant")

                self._content_header.set_title_widget(title_label)
            else:
                # Multiple participants - show as colored list
                title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                title_box.set_halign(Gtk.Align.CENTER)
                title_box.set_size_request(-1, 24)  # Ensure minimum height

                for i, participant in enumerate(chat.participants[:4]):
                    if i > 0:
                        sep = Gtk.Label(label=", ")
                        sep.add_css_class("title")
                        title_box.append(sep)

                    display_name = self._get_display_name(participant.address)
                    color = self._get_sender_color(participant.address)
                    name_label = Gtk.Label(label=display_name)
                    name_label.add_css_class("title")

                    css = Gtk.CssProvider()
                    css.load_from_data(f"""
                        .participant-title-{abs(hash(participant.address)) % 10000} {{
                            color: shade({color}, 0.6);
                        }}
                    """.encode())
                    Gtk.StyleContext.add_provider_for_display(
                        self.get_display(),
                        css,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )
                    name_label.add_css_class(f"participant-title-{abs(hash(participant.address)) % 10000}")
                    title_box.append(name_label)

                if len(chat.participants) > 4:
                    more = Gtk.Label(label=f" +{len(chat.participants) - 4}")
                    more.add_css_class("title")
                    more.add_css_class("dim-label")
                    title_box.append(more)

                self._content_header.set_title_widget(title_box)
        else:
            # Fallback
            self._content_title.set_title(chat.chat_identifier)
            self._content_title.set_subtitle("")
            self._content_header.set_title_widget(self._content_title)

    def _load_messages(self) -> None:
        """Load messages for the selected chat from cache, then sync."""
        if self._selected_chat is None:
            return

        chat_guid = self._selected_chat.guid

        def update_ui(messages: list[Message], replace: bool = False) -> bool:
            # Only update if still the same chat
            if self._selected_chat and self._selected_chat.guid == chat_guid:
                if replace:
                    # First load from cache - full replace is OK
                    self._messages = messages
                else:
                    # Server sync - merge to preserve locally added messages
                    existing_guids = {m.guid for m in self._messages}
                    new_guids = {m.guid for m in messages}

                    # Start with new server messages
                    merged = list(messages)

                    # Add any local messages not in server response (recently sent)
                    for msg in self._messages:
                        if msg.guid not in new_guids:
                            merged.append(msg)

                    # Sort by date (newest first for the DESC order we use)
                    merged.sort(key=lambda m: m.date_created, reverse=True)
                    self._messages = merged

                self._update_message_list()
            return False

        def load_and_sync() -> None:
            # Step 1: Load from cache immediately
            cached_messages = self._cache.get_chat_messages(chat_guid, limit=50)
            if cached_messages:
                GLib.idle_add(update_ui, cached_messages, True)  # replace=True for initial load

            # Step 2: Fetch from server
            async def _fetch() -> list[Message]:
                from ..api import BlueBubblesClient

                if not self.app.config.is_configured:
                    return []

                client = BlueBubblesClient(
                    self.app.config.server_url,  # type: ignore
                    self.app.config.password,  # type: ignore
                )
                try:
                    await client.connect()
                    return await client.get_chat_messages(chat_guid, limit=50)
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            try:
                messages = loop.run_until_complete(_fetch())
                if messages:
                    # Save to cache
                    self._cache.save_messages(chat_guid, messages)
                    GLib.idle_add(update_ui, messages, False)  # replace=False to merge with local
            except Exception as e:
                print(f"Error loading messages: {e}")
                # Keep cached messages if server fetch fails
            finally:
                loop.close()

        thread = threading.Thread(target=load_and_sync, daemon=True)
        thread.start()

    def _update_message_list(self) -> None:
        """Update the message list UI."""
        # Clear existing messages
        while True:
            child = self._message_list.get_first_child()
            if child is None:
                break
            self._message_list.remove(child)

        # Build reactions map: message GUID -> list of reaction messages
        # Note: associated_message_guid may have format "p:X/UUID" so we need to extract the UUID
        reactions_map: dict[str, list[Message]] = {}
        for message in self._messages:
            if message.is_reaction and message.associated_message_guid:
                guid = message.associated_message_guid
                # Extract UUID from "p:X/UUID" format if present
                if "/" in guid:
                    guid = guid.split("/")[-1]
                if guid not in reactions_map:
                    reactions_map[guid] = []
                reactions_map[guid].append(message)

        # Add message bubbles (in chronological order)
        pending_attachment_loads: list[tuple[Attachment, Gtk.Box]] = []
        pending_preview_loads: list[tuple[str, Gtk.Widget, Gtk.Box]] = []
        for message in reversed(self._messages):
            if message.is_reaction:
                continue  # Skip reaction messages themselves

            # Get reactions for this message
            reactions = reactions_map.get(message.guid)
            bubble = self._create_message_bubble(message, reactions)
            self._message_list.append(bubble)

            # Collect pending attachment loads
            if hasattr(bubble, '_pending_attachments'):
                pending_attachment_loads.extend(bubble._pending_attachments)  # type: ignore

            # Collect pending link preview loads
            if hasattr(bubble, '_pending_link_previews'):
                for url, placeholder in bubble._pending_link_previews:  # type: ignore
                    pending_preview_loads.append((url, placeholder, bubble._link_preview_bubble))  # type: ignore

        # Start async loading for uncached attachments
        for attachment, widget in pending_attachment_loads:
            self._load_attachment_async(attachment, widget)

        # Start async loading for link previews
        for url, placeholder, container in pending_preview_loads:
            self._load_link_preview_async(url, placeholder, container)

        # Scroll to bottom
        self._scroll_to_bottom()

    def select_chat_by_guid(self, chat_guid: str) -> None:
        """Select a chat by its GUID (used for notification clicks)."""
        # Find the chat in our list
        if chat_guid not in self._chats_by_guid:
            print(f"Chat {chat_guid} not found")
            return

        # Find the row in the list box
        row_index = 0
        for i, chat in enumerate(self._chats):
            if chat.guid == chat_guid:
                row_index = i
                break

        # Select the row
        row = self._chat_list.get_row_at_index(row_index)
        if row:
            self._chat_list.select_row(row)
            # Trigger the selection handler
            self._on_chat_selected(self._chat_list, row)

    def _should_notify(self, message: Message, chat_guid: str) -> bool:
        """Determine if we should show a notification for this message."""
        # Don't notify for our own messages
        if message.is_from_me:
            return False

        # Don't notify for reactions
        if message.is_reaction:
            return False

        # Check if window is focused and this chat is selected
        if self.is_active():
            if self._selected_chat and self._selected_chat.guid == chat_guid:
                # Window is focused and this chat is open - no notification needed
                return False

        return True

    def _send_notification(self, message: Message, chat_guid: str) -> None:
        """Send a desktop notification for a new message."""
        # Get sender name
        sender_name = "Unknown"
        if message.handle:
            sender_name = self._get_display_name(message.handle.address)
        elif chat_guid in self._chats_by_guid:
            # Use chat title if no handle
            sender_name = self._get_chat_title(self._chats_by_guid[chat_guid])

        # Get message preview
        body = message.text or ""
        if not body and message.attachments:
            # Describe attachment
            attachment = message.attachments[0]
            if attachment.is_image:
                body = "Sent an image"
            elif attachment.is_video:
                body = "Sent a video"
            else:
                body = f"Sent a file: {attachment.transfer_name or 'attachment'}"

        # Truncate long messages
        if len(body) > 100:
            body = body[:97] + "..."

        # Send via application
        self.app.show_message_notification(
            title=sender_name,
            body=body,
            chat_guid=chat_guid,
        )

    def _on_new_message_clicked(self, _button: Gtk.Button) -> None:
        """Handle click on the new message button."""
        self._show_new_conversation_dialog()

    def _show_new_conversation_dialog(self) -> None:
        """Show dialog to select recipients for a new conversation."""
        dialog = Adw.Dialog()
        dialog.set_title("New Message")
        dialog.set_content_width(400)
        dialog.set_content_height(450)

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        main_box.append(header)

        # Content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)

        # Selected recipients area (chips)
        recipients_flow = Gtk.FlowBox()
        recipients_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        recipients_flow.set_homogeneous(False)
        recipients_flow.set_max_children_per_line(10)
        recipients_flow.set_min_children_per_line(1)
        recipients_flow.set_row_spacing(4)
        recipients_flow.set_column_spacing(4)

        # Track selected recipients: list of (address, display_name)
        selected_recipients: list[tuple[str, str]] = []

        # Wrapper for recipients that hides when empty
        recipients_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        recipients_box.append(recipients_flow)
        recipients_box.set_visible(False)
        content_box.append(recipients_box)

        # Search entry for contacts
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Search contacts or enter number...")
        content_box.append(search_entry)

        # Results list
        results_scroll = Gtk.ScrolledWindow()
        results_scroll.set_vexpand(True)
        results_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        results_list = Gtk.ListBox()
        results_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        results_list.add_css_class("boxed-list")
        results_scroll.set_child(results_list)
        content_box.append(results_scroll)

        # Start conversation button
        start_button = Gtk.Button(label="Start Conversation")
        start_button.add_css_class("suggested-action")
        start_button.set_margin_top(8)
        start_button.set_sensitive(False)
        content_box.append(start_button)

        main_box.append(content_box)
        dialog.set_child(main_box)

        def update_start_button() -> None:
            """Update start button sensitivity."""
            start_button.set_sensitive(len(selected_recipients) > 0)

        def add_recipient(address: str, display_name: str) -> None:
            """Add a recipient chip."""
            # Check if already added
            if any(addr == address for addr, _ in selected_recipients):
                return

            selected_recipients.append((address, display_name))

            # Create chip
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            chip.add_css_class("card")
            chip.set_margin_start(2)
            chip.set_margin_end(2)
            chip.set_margin_top(2)
            chip.set_margin_bottom(2)

            # Apply chip styling
            css = Gtk.CssProvider()
            css.load_from_data(b"""
                .recipient-chip {
                    padding: 4px 8px;
                    border-radius: 16px;
                    background-color: @accent_bg_color;
                    color: @accent_fg_color;
                }
            """)
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            chip.add_css_class("recipient-chip")

            label = Gtk.Label(label=display_name)
            chip.append(label)

            # Remove button
            remove_btn = Gtk.Button()
            remove_btn.set_icon_name("window-close-symbolic")
            remove_btn.add_css_class("flat")
            remove_btn.add_css_class("circular")

            def on_remove(_btn: Gtk.Button, addr: str = address) -> None:
                # Remove from list
                nonlocal selected_recipients
                selected_recipients = [(a, n) for a, n in selected_recipients if a != addr]
                # Remove chip from flow box
                child = chip.get_parent()
                if child:
                    recipients_flow.remove(child)
                # Hide if empty
                if not selected_recipients:
                    recipients_box.set_visible(False)
                update_start_button()

            remove_btn.connect("clicked", on_remove)
            chip.append(remove_btn)

            recipients_flow.append(chip)
            recipients_box.set_visible(True)

            # Clear search
            search_entry.set_text("")
            update_results("")
            update_start_button()

        def update_results(query: str) -> None:
            """Update the results list based on search query."""
            # Clear existing results
            while True:
                row = results_list.get_row_at_index(0)
                if row is None:
                    break
                results_list.remove(row)

            if not query:
                return

            query_lower = query.lower()
            matches: list[tuple[str, str, str]] = []  # (address, display_name, subtitle)

            # Search through contacts
            seen_addresses: set[str] = set()
            for address, name in self._contacts.items():
                if address in seen_addresses:
                    continue
                # Skip variants (only show one per contact)
                if any(addr == address for addr, _ in selected_recipients):
                    continue

                # Match against name or address
                if query_lower in name.lower() or query_lower in address.lower():
                    # Determine if this is a phone or email
                    if "@" in address:
                        subtitle = address
                    else:
                        # Format phone number for display
                        subtitle = address
                    matches.append((address, name, subtitle))
                    seen_addresses.add(address)

                    # Also mark normalized variants as seen
                    for variant in self._normalize_phone(address):
                        seen_addresses.add(variant)

            # If query looks like a phone number or email, add option to use it directly
            is_phone = query.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").isdigit()
            is_email = "@" in query and "." in query

            if (is_phone or is_email) and query not in seen_addresses:
                matches.insert(0, (query, query, "Send to this address"))

            # Limit results
            for address, name, subtitle in matches[:10]:
                row = Gtk.ListBoxRow()
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row_box.set_margin_start(12)
                row_box.set_margin_end(12)
                row_box.set_margin_top(8)
                row_box.set_margin_bottom(8)

                # Avatar
                avatar = Adw.Avatar(size=32, text=name, show_initials=True)
                row_box.append(avatar)

                # Text
                text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                name_label = Gtk.Label(label=name, xalign=0)
                name_label.add_css_class("heading")
                text_box.append(name_label)

                if subtitle != name:
                    subtitle_label = Gtk.Label(label=subtitle, xalign=0)
                    subtitle_label.add_css_class("caption")
                    subtitle_label.add_css_class("dim-label")
                    text_box.append(subtitle_label)

                row_box.append(text_box)
                row.set_child(row_box)

                # Store data on row
                row._contact_address = address  # type: ignore
                row._contact_name = name  # type: ignore

                results_list.append(row)

        def on_search_changed(entry: Gtk.SearchEntry) -> None:
            update_results(entry.get_text())

        def on_result_selected(listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
            if row is None:
                return
            address = row._contact_address  # type: ignore
            name = row._contact_name  # type: ignore
            add_recipient(address, name)
            # Deselect
            listbox.unselect_all()

        def on_start_clicked(_button: Gtk.Button) -> None:
            if not selected_recipients:
                return

            addresses = [addr for addr, _ in selected_recipients]
            names = [name for _, name in selected_recipients]

            # Check if conversation already exists
            existing_chat_guid = self._find_existing_chat(addresses)

            if existing_chat_guid:
                # Jump to existing conversation
                dialog.close()
                self.select_chat_by_guid(existing_chat_guid)
            else:
                # Create a pending conversation
                dialog.close()
                self._create_pending_conversation(addresses, names)

        # Connect signals
        search_entry.connect("search-changed", on_search_changed)
        results_list.connect("row-selected", on_result_selected)
        start_button.connect("clicked", on_start_clicked)

        # Handle Enter in search to add first result or raw input
        def on_search_activate(entry: Gtk.SearchEntry) -> None:
            query = entry.get_text().strip()
            if not query:
                return

            # Check if there's a selected row
            row = results_list.get_selected_row()
            if row:
                on_result_selected(results_list, row)
                return

            # Check first result
            first_row = results_list.get_row_at_index(0)
            if first_row:
                on_result_selected(results_list, first_row)
                return

            # If query looks like a phone/email, add it directly
            is_phone = query.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").isdigit()
            is_email = "@" in query and "." in query
            if is_phone or is_email:
                add_recipient(query, query)

        search_entry.connect("activate", on_search_activate)

        dialog.present(self)
        search_entry.grab_focus()

    def _find_existing_chat(self, addresses: list[str]) -> str | None:
        """Find an existing chat with the given participants."""
        if len(addresses) == 1:
            # Single recipient - look for 1:1 chat
            target = addresses[0]
            target_variants = set(self._normalize_phone(target))
            target_variants.add(target)

            for chat in self._chats:
                if chat.is_group:
                    continue
                if not chat.participants or len(chat.participants) != 1:
                    continue

                participant_addr = chat.participants[0].address
                participant_variants = set(self._normalize_phone(participant_addr))
                participant_variants.add(participant_addr)

                # Check if any variants match
                if target_variants & participant_variants:
                    return chat.guid
        else:
            # Group chat - look for matching participants
            target_set = set(addresses)
            for chat in self._chats:
                if not chat.is_group:
                    continue
                if not chat.participants:
                    continue

                chat_addresses = {p.address for p in chat.participants}
                if chat_addresses == target_set:
                    return chat.guid

        return None

    def _create_pending_conversation(self, addresses: list[str], names: list[str]) -> None:
        """Create a pending conversation that will be created on first message."""
        # Generate a temporary GUID for this pending conversation
        import uuid
        pending_guid = f"pending-{uuid.uuid4()}"

        # Create display name
        if len(names) == 1:
            display_name = names[0]
        else:
            display_name = ", ".join(names[:3])
            if len(names) > 3:
                display_name += f" +{len(names) - 3}"

        # Store pending conversation info
        self._pending_conversation = {
            "guid": pending_guid,
            "addresses": addresses,
            "names": names,
            "display_name": display_name,
        }

        # Create a fake chat row in the sidebar
        row = Gtk.ListBoxRow()
        row.chat = None  # type: ignore
        row._is_pending = True  # type: ignore
        row._pending_guid = pending_guid  # type: ignore

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Avatar
        avatar = Adw.Avatar(size=40, text=display_name, show_initials=True)
        box.append(avatar)

        # Text content
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        name_label = Gtk.Label(label=display_name, xalign=0)
        name_label.set_ellipsize(3)
        name_label.add_css_class("heading")
        text_box.append(name_label)

        preview_label = Gtk.Label(label="New conversation", xalign=0)
        preview_label.set_ellipsize(3)
        preview_label.add_css_class("dim-label")
        preview_label.add_css_class("caption")
        text_box.append(preview_label)

        box.append(text_box)
        row.set_child(box)

        # Insert at top of chat list
        self._chat_list.prepend(row)
        self._chat_list.select_row(row)

        # Set up the content area for this pending conversation
        self._selected_chat = None
        self._messages = []

        # Update header
        self._content_title.set_title(display_name)
        self._content_title.set_subtitle("New conversation")
        self._content_header.set_title_widget(self._content_title)

        # Clear message list
        while True:
            child = self._message_list.get_first_child()
            if child is None:
                break
            self._message_list.remove(child)

        # Show empty state for new conversation
        empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty_box.set_valign(Gtk.Align.CENTER)
        empty_box.set_halign(Gtk.Align.CENTER)
        empty_box.set_vexpand(True)

        icon = Gtk.Image.new_from_icon_name("mail-send-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        empty_box.append(icon)

        hint_label = Gtk.Label(label="Send a message to start the conversation")
        hint_label.add_css_class("dim-label")
        empty_box.append(hint_label)

        self._message_list.append(empty_box)

        # Show compose box
        self._compose_box.set_visible(True)
        self._message_entry.grab_focus()

    def _on_send_message(self, _widget: Any) -> None:
        """Handle sending a message."""
        # Check if this is a pending conversation
        if hasattr(self, '_pending_conversation') and self._pending_conversation:
            self._send_pending_conversation_message()
            return

        if self._selected_chat is None:
            return

        text = self._message_entry.get_text().strip()
        if not text:
            return

        chat_guid = self._selected_chat.guid

        # Clear entry immediately - don't disable to avoid blocking
        self._message_entry.set_text("")

        # Add an optimistic local message immediately for responsiveness
        # This will be updated when the server confirms

        def send_message() -> None:
            async def _send() -> Message | None:
                client = self.app.get_client()
                if client is None:
                    return None
                try:
                    await client.connect()
                    return await client.send_message(chat_guid, text)
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                message = loop.run_until_complete(_send())
            except Exception as e:
                print(f"Error sending message: {e}")
                message = None
            finally:
                loop.close()

            def update_ui() -> bool:
                if message:
                    # Save to cache
                    self._cache.save_messages(chat_guid, [message])

                    # Update chat list with new last message
                    if chat_guid in self._chats_by_guid:
                        chat = self._chats_by_guid[chat_guid]
                        chat_data = chat.model_dump(by_alias=True)
                        chat_data["lastMessage"] = message.model_dump(by_alias=True)
                        updated_chat = Chat(**chat_data)
                        self._chats_by_guid[chat_guid] = updated_chat

                        # Move chat to top of list
                        self._chats = [c for c in self._chats if c.guid != chat_guid]
                        self._chats.insert(0, updated_chat)

                        # Rebuild the chat list UI
                        self._rebuild_chat_list_preserving_selection()

                    # Check if message already exists (might have arrived via socket)
                    if not any(m.guid == message.guid for m in self._messages):
                        # Add to message list
                        self._messages.insert(0, message)
                        bubble = self._create_message_bubble(message, None)
                        self._message_list.append(bubble)

                    # Scroll to bottom
                    self._scroll_to_bottom()

                return False

            GLib.idle_add(update_ui)

        thread = threading.Thread(target=send_message, daemon=True)
        thread.start()

    def _send_pending_conversation_message(self) -> None:
        """Send the first message to create a new conversation."""
        text = self._message_entry.get_text().strip()
        if not text:
            return

        pending = self._pending_conversation
        addresses = pending["addresses"]
        pending_guid = pending["guid"]

        # Clear entry immediately - don't disable
        self._message_entry.set_text("")

        def do_send() -> None:
            async def _send() -> str | None:
                client = self.app.get_client()
                if client is None:
                    return None

                try:
                    await client.connect()
                    # Create new chat with message
                    data = {
                        "participants": addresses,
                        "message": text,
                        "method": "private-api",
                    }
                    response = await client._post("chat/new", data)
                    if response.data:
                        return response.data.get("guid")
                    return None
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                chat_guid = loop.run_until_complete(_send())

                def update_ui() -> bool:
                    if chat_guid:
                        # Clear pending state
                        self._pending_conversation = None

                        # Remove the pending row from sidebar
                        row = self._chat_list.get_row_at_index(0)
                        while row:
                            if hasattr(row, '_is_pending') and row._is_pending:
                                if row._pending_guid == pending_guid:
                                    self._chat_list.remove(row)
                                    break
                            row = self._chat_list.get_row_at_index(
                                self._chat_list.get_row_at_index(0) and 1 or 0
                            )
                            # Simple approach: just remove first pending row
                            break

                        # Reload chats and select the new one
                        self._load_chats()

                        def select_chat() -> bool:
                            self.select_chat_by_guid(chat_guid)
                            return False
                        GLib.timeout_add(300, select_chat)
                    else:
                        # Failed - let user retry
                        self._message_entry.set_text(text)

                    return False

                GLib.idle_add(update_ui)
            except Exception as e:
                print(f"Error creating conversation: {e}")

                def show_error() -> bool:
                    self._message_entry.set_text(text)
                    return False

                GLib.idle_add(show_error)
            finally:
                loop.close()

        thread = threading.Thread(target=do_send, daemon=True)
        thread.start()
