"""Layer Shell side panel for quick messaging in Hyprland."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

# Try to import Gtk4LayerShell - it's optional
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False
    print("gtk4-layer-shell not available - panel will run as regular window")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..api import BlueBubblesClient
from ..api.models import Chat, Message
from ..state.cache import Cache
from ..utils.config import Config


# IPC socket path
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "bluebubbles-panel.sock"

# Waybar output path
WAYBAR_OUTPUT_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "bluebubbles-waybar.json"

# Panel position setting file
PANEL_CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bluebubbles" / "panel.json"


class SidePanelApplication(Adw.Application):
    """Side panel application for quick messaging."""

    def __init__(self, position: str = "left") -> None:
        super().__init__(
            application_id="com.bluebubbles.Panel",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.config = Config()
        self.position = position
        self._panel_window: SidePanelWindow | None = None
        self._ipc_server: socket.socket | None = None
        self._ipc_thread: threading.Thread | None = None

    def do_activate(self) -> None:
        """Called when the application is activated."""
        if not self._panel_window:
            self._panel_window = SidePanelWindow(application=self, position=self.position)
        self._panel_window.present()

    def do_startup(self) -> None:
        """Called when the application starts."""
        Adw.Application.do_startup(self)
        self._start_ipc_server()
        self._setup_actions()

    def do_shutdown(self) -> None:
        """Called when the application shuts down."""
        self._stop_ipc_server()
        Adw.Application.do_shutdown(self)

    def _setup_actions(self) -> None:
        """Set up application actions."""
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

        toggle_action = Gio.SimpleAction.new("toggle", None)
        toggle_action.connect("activate", self._on_toggle)
        self.add_action(toggle_action)

    def _on_toggle(self, _action: Any, _param: Any) -> None:
        """Toggle panel visibility with slide animation."""
        if self._panel_window:
            if self._panel_window._is_shown:
                self._panel_window.slide_out()
            else:
                self._panel_window.slide_in()

    def _start_ipc_server(self) -> None:
        """Start the IPC server for toggle commands."""
        # Remove existing socket
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        self._ipc_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._ipc_server.bind(str(SOCKET_PATH))
        self._ipc_server.listen(1)
        self._ipc_server.setblocking(False)

        def ipc_loop() -> None:
            while self._ipc_server:
                try:
                    conn, _ = self._ipc_server.accept()
                    data = conn.recv(1024).decode().strip()
                    if data == "toggle":
                        GLib.idle_add(self._on_toggle, None, None)
                        conn.send(b"ok\n")
                    elif data == "show":
                        GLib.idle_add(lambda: self._panel_window and self._panel_window.slide_in())
                        conn.send(b"ok\n")
                    elif data == "hide":
                        GLib.idle_add(lambda: self._panel_window and self._panel_window.slide_out())
                        conn.send(b"ok\n")
                    elif data == "status":
                        visible = self._panel_window._is_shown if self._panel_window else False
                        conn.send(f"{{'visible': {str(visible).lower()}}}\n".encode())
                    conn.close()
                except BlockingIOError:
                    import time
                    time.sleep(0.1)
                except Exception:
                    break

        self._ipc_thread = threading.Thread(target=ipc_loop, daemon=True)
        self._ipc_thread.start()

    def _stop_ipc_server(self) -> None:
        """Stop the IPC server."""
        if self._ipc_server:
            try:
                self._ipc_server.close()
            except Exception:
                pass
            self._ipc_server = None

        if SOCKET_PATH.exists():
            try:
                SOCKET_PATH.unlink()
            except Exception:
                pass


class SidePanelWindow(Adw.ApplicationWindow):
    """The side panel window."""

    # Animation settings
    ANIMATION_DURATION = 200  # ms
    MARGIN_VISIBLE = 10
    MARGIN_HIDDEN = -500  # Fully off-screen (panel width + extra)

    def __init__(self, application: SidePanelApplication, position: str = "left") -> None:
        super().__init__(application=application)
        self.app = application
        self._cache = Cache()
        self._config = application.config
        self._position = position
        self._chats: list[Chat] = []
        self._selected_chat: Chat | None = None
        self._contacts: dict[str, str] = {}
        self._unread_count = 0
        self._messages: list[Message] = []
        self._is_animating = False
        self._is_shown = False  # Track logical visibility (not GTK visibility)
        self._slide_animation: Adw.TimedAnimation | None = None

        self._setup_window()
        self._build_ui()
        self._load_data()

    def _setup_window(self) -> None:
        """Configure window properties and Layer Shell if available."""
        self.set_title("BlueBubbles")

        # Size based on position
        if self._position in ("left", "right"):
            self.set_default_size(380, 600)
        else:
            self.set_default_size(600, 400)

        if HAS_LAYER_SHELL:
            # Initialize Layer Shell
            Gtk4LayerShell.init_for_window(self)
            Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.TOP)

            # Configure anchoring based on position
            if self._position == "left":
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, True)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.LEFT, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.BOTTOM, 10)
            elif self._position == "right":
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, True)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.RIGHT, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.BOTTOM, 10)
            elif self._position == "top":
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.TOP, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.TOP, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.LEFT, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.RIGHT, 10)
            elif self._position == "bottom":
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.BOTTOM, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.LEFT, True)
                Gtk4LayerShell.set_anchor(self, Gtk4LayerShell.Edge.RIGHT, True)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.BOTTOM, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.LEFT, 10)
                Gtk4LayerShell.set_margin(self, Gtk4LayerShell.Edge.RIGHT, 10)

            # Set namespace for window rules
            Gtk4LayerShell.set_namespace(self, "bluebubbles-panel")

            # Allow keyboard input
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.ON_DEMAND
            )

        # Handle keyboard events
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _get_slide_edge(self) -> "Gtk4LayerShell.Edge | None":
        """Get the edge to animate for sliding."""
        if not HAS_LAYER_SHELL:
            return None
        edge_map = {
            "left": Gtk4LayerShell.Edge.LEFT,
            "right": Gtk4LayerShell.Edge.RIGHT,
            "top": Gtk4LayerShell.Edge.TOP,
            "bottom": Gtk4LayerShell.Edge.BOTTOM,
        }
        return edge_map.get(self._position)

    def _set_slide_margin(self, value: float) -> None:
        """Set the margin for slide animation."""
        if not HAS_LAYER_SHELL:
            return
        edge = self._get_slide_edge()
        if edge is not None:
            Gtk4LayerShell.set_margin(self, edge, int(value))

    def slide_in(self) -> None:
        """Slide the panel into view."""
        if self._is_shown or self._is_animating:
            return

        if not HAS_LAYER_SHELL:
            self._is_shown = True
            Adw.ApplicationWindow.present(self)
            return

        # Start from hidden position
        self._set_slide_margin(self.MARGIN_HIDDEN)
        Adw.ApplicationWindow.present(self)

        # Animate to visible
        self._is_animating = True
        target = Adw.CallbackAnimationTarget.new(self._set_slide_margin)
        self._slide_animation = Adw.TimedAnimation.new(
            self,
            self.MARGIN_HIDDEN,
            self.MARGIN_VISIBLE,
            self.ANIMATION_DURATION,
            target,
        )
        self._slide_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        def on_done(_anim: Adw.TimedAnimation) -> None:
            self._is_animating = False
            self._is_shown = True

        self._slide_animation.connect("done", on_done)
        self._slide_animation.play()

    def slide_out(self) -> None:
        """Slide the panel out of view (stays off-screen, no fade)."""
        if not self._is_shown or self._is_animating:
            return

        if not HAS_LAYER_SHELL:
            self._is_shown = False
            Adw.ApplicationWindow.hide(self)
            return

        self._is_animating = True
        self._is_shown = False
        target = Adw.CallbackAnimationTarget.new(self._set_slide_margin)
        self._slide_animation = Adw.TimedAnimation.new(
            self,
            self.MARGIN_VISIBLE,
            self.MARGIN_HIDDEN,
            self.ANIMATION_DURATION,
            target,
        )
        self._slide_animation.set_easing(Adw.Easing.EASE_IN_CUBIC)
        self._slide_animation.connect("done", lambda _: setattr(self, "_is_animating", False))
        self._slide_animation.play()

    def present(self) -> None:
        """Override present to slide in."""
        self.slide_in()

    def hide(self) -> None:
        """Override hide to slide out."""
        self.slide_out()

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle keyboard navigation."""
        # Escape - hide panel or go back
        if keyval == Gdk.KEY_Escape:
            if self._nav_view.get_visible_page() == self._conversation_page:
                self._go_back_to_list()
            else:
                self.hide()
            return True

        # Tab - go back to list from conversation
        if keyval == Gdk.KEY_Tab:
            if self._nav_view.get_visible_page() == self._conversation_page:
                self._go_back_to_list()
                return True

        # Only handle navigation keys when on list page and entry not focused
        if self._nav_view.get_visible_page() == self._list_page:
            focused = self.get_focus()
            is_entry_focused = isinstance(focused, Gtk.Entry) or isinstance(focused, Gtk.Text)

            if not is_entry_focused:
                # Up/Down - navigate chat list
                if keyval == Gdk.KEY_Up or keyval == Gdk.KEY_k:
                    self._navigate_list(-1)
                    return True
                elif keyval == Gdk.KEY_Down or keyval == Gdk.KEY_j:
                    self._navigate_list(1)
                    return True
                # Enter/Space - open selected chat
                elif keyval in (Gdk.KEY_Return, Gdk.KEY_space, Gdk.KEY_KP_Enter):
                    row = self._chat_list.get_selected_row()
                    if row:
                        self._open_conversation(row.chat)  # type: ignore
                    return True

        return False

    def _navigate_list(self, direction: int) -> None:
        """Navigate up/down in the chat list."""
        current_row = self._chat_list.get_selected_row()
        if current_row is None:
            # Select first row
            first_row = self._chat_list.get_row_at_index(0)
            if first_row:
                self._chat_list.select_row(first_row)
            return

        current_index = current_row.get_index()
        new_index = current_index + direction

        # Clamp to valid range
        new_index = max(0, new_index)

        new_row = self._chat_list.get_row_at_index(new_index)
        if new_row:
            self._chat_list.select_row(new_row)
            # Scroll to make visible
            new_row.grab_focus()

    def _build_ui(self) -> None:
        """Build the panel UI with navigation view."""
        # Navigation view for sliding between pages
        self._nav_view = Adw.NavigationView()

        # Build list page
        self._list_page = self._build_list_page()
        self._nav_view.add(self._list_page)

        # Build conversation page (added dynamically when needed)
        self._conversation_page = self._build_conversation_page()

        # Apply styling
        self._apply_css()

        self.set_content(self._nav_view)

    def _build_list_page(self) -> Adw.NavigationPage:
        """Build the chat list page."""
        page = Adw.NavigationPage(title="Messages", tag="list")

        toolbar_view = Adw.ToolbarView()

        # Header
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # Refresh button
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda _: self._load_data())
        header.pack_start(refresh_btn)

        toolbar_view.add_top_bar(header)

        # Chat list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._chat_list = Gtk.ListBox()
        self._chat_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._chat_list.add_css_class("navigation-sidebar")
        self._chat_list.connect("row-activated", self._on_chat_activated)
        scrolled.set_child(self._chat_list)

        toolbar_view.set_content(scrolled)
        page.set_child(toolbar_view)

        return page

    def _build_conversation_page(self) -> Adw.NavigationPage:
        """Build the conversation view page."""
        page = Adw.NavigationPage(title="Conversation", tag="conversation")

        toolbar_view = Adw.ToolbarView()

        # Header with back button
        header = Adw.HeaderBar()
        self._conv_title = Gtk.Label(label="")
        self._conv_title.add_css_class("title")
        self._conv_title.set_ellipsize(3)
        header.set_title_widget(self._conv_title)

        toolbar_view.add_top_bar(header)

        # Main content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Messages list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._message_list = Gtk.ListBox()
        self._message_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._message_list.add_css_class("boxed-list")
        scrolled.set_child(self._message_list)
        content_box.append(scrolled)

        # Message entry
        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry_box.set_margin_start(12)
        entry_box.set_margin_end(12)
        entry_box.set_margin_top(8)
        entry_box.set_margin_bottom(12)

        self._message_entry = Gtk.Entry()
        self._message_entry.set_hexpand(True)
        self._message_entry.set_placeholder_text("Message...")
        self._message_entry.connect("activate", self._on_send_message)
        entry_box.append(self._message_entry)

        send_btn = Gtk.Button(icon_name="go-up-symbolic")
        send_btn.add_css_class("suggested-action")
        send_btn.add_css_class("circular")
        send_btn.connect("clicked", self._on_send_message)
        entry_box.append(send_btn)

        content_box.append(entry_box)

        toolbar_view.set_content(content_box)
        page.set_child(toolbar_view)

        return page

    def _apply_css(self) -> None:
        """Apply custom CSS styling."""
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .panel-window {
                background-color: alpha(@window_bg_color, 0.95);
            }
            .unread-badge {
                background-color: @accent_bg_color;
                color: @accent_fg_color;
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 0.8em;
                font-weight: bold;
            }
            .message-row {
                padding: 8px 12px;
            }
            .message-from-me {
                background-color: alpha(@accent_bg_color, 0.3);
                border-radius: 12px;
                margin-left: 40px;
            }
            .message-from-other {
                background-color: alpha(@view_bg_color, 0.5);
                border-radius: 12px;
                margin-right: 40px;
            }
            .message-text {
                padding: 8px 12px;
            }
            .message-time {
                font-size: 0.8em;
                padding: 4px 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _load_data(self) -> None:
        """Load chats and contacts."""
        if not self._config.is_configured:
            return

        def fetch() -> None:
            async def _fetch() -> tuple[list[Chat], dict[str, str]]:
                client = BlueBubblesClient(
                    self._config.server_url,  # type: ignore
                    self._config.password,  # type: ignore
                )
                try:
                    await client.connect()
                    chats = await client.get_chats(limit=20)
                    contacts = await client.get_contacts()

                    # Build contacts dict
                    contacts_dict: dict[str, str] = {}
                    for contact in contacts:
                        if contact.display_name:
                            for phone in contact.phones:
                                addr = phone.get("address", "")
                                if addr:
                                    contacts_dict[addr] = contact.display_name
                            for email in contact.emails:
                                addr = email.get("address", "")
                                if addr:
                                    contacts_dict[addr.lower()] = contact.display_name

                    return chats, contacts_dict
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                chats, contacts = loop.run_until_complete(_fetch())

                def update_ui() -> bool:
                    self._chats = chats
                    self._contacts = contacts
                    self._update_chat_list()
                    self._update_waybar_output()
                    return False

                GLib.idle_add(update_ui)
            except Exception as e:
                print(f"Error loading data: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _update_chat_list(self) -> None:
        """Update the chat list."""
        # Clear existing
        while True:
            row = self._chat_list.get_row_at_index(0)
            if row is None:
                break
            self._chat_list.remove(row)

        # Count unread based on last message read status
        self._unread_count = 0

        for chat in self._chats:
            row = self._create_chat_row(chat)
            self._chat_list.append(row)

            # Check if last message is unread (from someone else and not read)
            if chat.last_message and not chat.last_message.is_from_me and not chat.last_message.is_read:
                self._unread_count += 1

    def _create_chat_row(self, chat: Chat) -> Gtk.ListBoxRow:
        """Create a compact chat row."""
        row = Gtk.ListBoxRow()
        row.chat = chat  # type: ignore

        # Check if has unread message
        has_unread = (
            chat.last_message
            and not chat.last_message.is_from_me
            and not chat.last_message.is_read
        )

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Get display name
        title = self._get_chat_title(chat)

        # Avatar
        avatar = Adw.Avatar(size=36, text=title, show_initials=True)
        box.append(avatar)

        # Text content
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        name_label = Gtk.Label(label=title, xalign=0)
        name_label.set_ellipsize(3)
        if has_unread:
            name_label.add_css_class("heading")
        text_box.append(name_label)

        # Preview
        if chat.last_message:
            preview = chat.last_message.text or "(attachment)"
            if len(preview) > 40:
                preview = preview[:37] + "..."
        else:
            preview = ""

        preview_label = Gtk.Label(label=preview, xalign=0)
        preview_label.set_ellipsize(3)
        preview_label.add_css_class("dim-label")
        preview_label.add_css_class("caption")
        text_box.append(preview_label)

        box.append(text_box)

        # Unread badge
        if has_unread:
            badge = Gtk.Label(label="●")
            badge.add_css_class("unread-badge")
            box.append(badge)

        row.set_child(box)
        return row

    def _get_chat_title(self, chat: Chat) -> str:
        """Get display title for a chat."""
        if chat.display_name:
            return chat.display_name

        if chat.participants:
            names = []
            for p in chat.participants[:3]:
                name = self._contacts.get(p.address, p.address)
                names.append(name)
            title = ", ".join(names)
            if len(chat.participants) > 3:
                title += f" +{len(chat.participants) - 3}"
            return title

        return chat.chat_identifier

    def _on_chat_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Handle chat row activation (click or enter)."""
        if row is None:
            return
        self._open_conversation(row.chat)  # type: ignore

    def _open_conversation(self, chat: Chat) -> None:
        """Open the conversation view for a chat."""
        self._selected_chat = chat
        title = self._get_chat_title(chat)
        self._conv_title.set_text(title)

        # Clear previous messages
        while True:
            row = self._message_list.get_row_at_index(0)
            if row is None:
                break
            self._message_list.remove(row)

        # Navigate to conversation page
        self._nav_view.push(self._conversation_page)

        # Load messages
        self._load_messages(chat.guid)

        # Focus entry after a short delay
        GLib.timeout_add(100, lambda: self._message_entry.grab_focus() or False)

    def _load_messages(self, chat_guid: str) -> None:
        """Load messages for a chat."""
        def fetch() -> None:
            async def _fetch() -> list[Message]:
                client = BlueBubblesClient(
                    self._config.server_url,  # type: ignore
                    self._config.password,  # type: ignore
                )
                try:
                    await client.connect()
                    messages = await client.get_chat_messages(chat_guid, limit=30)
                    return list(reversed(messages))  # Oldest first
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                messages = loop.run_until_complete(_fetch())

                def update_ui() -> bool:
                    self._messages = messages
                    self._update_message_list()
                    return False

                GLib.idle_add(update_ui)
            except Exception as e:
                print(f"Error loading messages: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _update_message_list(self) -> None:
        """Update the message list."""
        # Clear existing
        while True:
            row = self._message_list.get_row_at_index(0)
            if row is None:
                break
            self._message_list.remove(row)

        for msg in self._messages:
            # Skip reactions
            if msg.is_reaction:
                continue
            row = self._create_message_row(msg)
            self._message_list.append(row)

        # Scroll to bottom
        def scroll_to_bottom() -> bool:
            adj = self._message_list.get_parent().get_vadjustment()  # type: ignore
            if adj:
                adj.set_value(adj.get_upper())
            return False
        GLib.timeout_add(100, scroll_to_bottom)

    def _create_message_row(self, msg: Message) -> Gtk.ListBoxRow:
        """Create a message row."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("message-row")

        if msg.is_from_me:
            box.add_css_class("message-from-me")
            box.set_halign(Gtk.Align.END)
        else:
            box.add_css_class("message-from-other")
            box.set_halign(Gtk.Align.START)

        # Message text
        if msg.text:
            text_label = Gtk.Label(label=msg.text, xalign=0 if not msg.is_from_me else 1)
            text_label.set_wrap(True)
            text_label.set_wrap_mode(2)  # WORD_CHAR
            text_label.add_css_class("message-text")
            text_label.set_max_width_chars(35)
            box.append(text_label)
        elif msg.has_attachments:
            text_label = Gtk.Label(label="(attachment)", xalign=0 if not msg.is_from_me else 1)
            text_label.add_css_class("message-text")
            text_label.add_css_class("dim-label")
            box.append(text_label)

        # Time
        time_str = msg.date_created_dt.strftime("%H:%M")
        time_label = Gtk.Label(label=time_str, xalign=1 if msg.is_from_me else 0)
        time_label.add_css_class("message-time")
        time_label.add_css_class("dim-label")
        box.append(time_label)

        row.set_child(box)
        return row

    def _go_back_to_list(self) -> None:
        """Navigate back to the chat list."""
        self._nav_view.pop()
        self._selected_chat = None
        # Focus the list
        GLib.timeout_add(100, lambda: self._chat_list.grab_focus() or False)

    def _on_send_message(self, _widget: Any) -> None:
        """Send a quick reply."""
        if not self._selected_chat:
            return

        text = self._message_entry.get_text().strip()
        if not text:
            return

        chat_guid = self._selected_chat.guid
        self._message_entry.set_text("")

        def send() -> None:
            async def _send() -> Message | None:
                client = BlueBubblesClient(
                    self._config.server_url,  # type: ignore
                    self._config.password,  # type: ignore
                )
                try:
                    await client.connect()
                    return await client.send_message(chat_guid, text)
                except Exception as e:
                    print(f"Error sending: {e}")
                    return None
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                msg = loop.run_until_complete(_send())
                if msg:
                    def add_message() -> bool:
                        self._messages.append(msg)
                        row = self._create_message_row(msg)
                        self._message_list.append(row)
                        # Scroll to bottom
                        adj = self._message_list.get_parent().get_vadjustment()  # type: ignore
                        if adj:
                            adj.set_value(adj.get_upper())
                        return False
                    GLib.idle_add(add_message)
            finally:
                loop.close()

        thread = threading.Thread(target=send, daemon=True)
        thread.start()

    def _update_waybar_output(self) -> None:
        """Update the waybar JSON output file."""
        try:
            output = {
                "text": str(self._unread_count) if self._unread_count > 0 else "",
                "tooltip": f"{self._unread_count} unread messages" if self._unread_count else "No unread messages",
                "class": "has-unread" if self._unread_count > 0 else "no-unread",
                "alt": "unread" if self._unread_count > 0 else "read",
            }

            WAYBAR_OUTPUT_PATH.write_text(json.dumps(output))
        except Exception as e:
            print(f"Error updating waybar output: {e}")


def send_ipc_command(command: str) -> str | None:
    """Send a command to a running panel instance."""
    if not SOCKET_PATH.exists():
        return None

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(SOCKET_PATH))
        sock.send(f"{command}\n".encode())
        response = sock.recv(1024).decode().strip()
        sock.close()
        return response
    except Exception:
        return None


def run_panel(toggle: bool = False, position: str = "left") -> int:
    """Run the side panel application."""
    if toggle:
        # Try to toggle existing instance
        response = send_ipc_command("toggle")
        if response == "ok":
            return 0
        # No running instance, start new one

    app = SidePanelApplication(position=position)
    return app.run([])
