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
from ..api.websocket import BlueBubblesSocket
from ..state.cache import Cache
from ..utils.config import Config


# IPC socket path
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "bluebubbles-panel.sock"


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
        self._messages: list[Message] = []
        self._is_animating = False
        self._is_shown = False  # Track logical visibility (not GTK visibility)
        self._slide_animation: Adw.TimedAnimation | None = None
        self._socket: BlueBubblesSocket | None = None
        self._socket_thread: threading.Thread | None = None

        self._setup_window()
        self._build_ui()
        self._load_data()
        self._connect_socket()

    def _setup_window(self) -> None:
        """Configure window properties and Layer Shell if available."""
        self.set_title("BlueBubbles")

        # Size based on position
        if self._position in ("left", "right"):
            self.set_default_size(380, 600)
        else:
            self.set_default_size(600, 400)

        # Track if layer shell is actually working (not just imported)
        self._layer_shell_active = False

        if HAS_LAYER_SHELL:
            # Initialize Layer Shell
            Gtk4LayerShell.init_for_window(self)
            # Check if it actually worked
            self._layer_shell_active = Gtk4LayerShell.is_layer_window(self)
            if not self._layer_shell_active:
                print("gtk4-layer-shell initialization failed - running as regular window")
                return
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

            # Start with no keyboard grab (panel starts hidden)
            # Keyboard will be enabled when panel slides in
            Gtk4LayerShell.set_keyboard_mode(
                self, Gtk4LayerShell.KeyboardMode.NONE
            )

        # Handle keyboard events
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _get_slide_edge(self) -> "Gtk4LayerShell.Edge | None":
        """Get the edge to animate for sliding."""
        if not self._layer_shell_active:
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

        if not self._layer_shell_active:
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
            # Enable keyboard input when fully shown
            if self._layer_shell_active:
                Gtk4LayerShell.set_keyboard_mode(
                    self, Gtk4LayerShell.KeyboardMode.ON_DEMAND
                )

        self._slide_animation.connect("done", on_done)
        self._slide_animation.play()

    def slide_out(self) -> None:
        """Slide the panel out of view (stays off-screen, no fade)."""
        if not self._is_shown or self._is_animating:
            return

        if not self._layer_shell_active:
            self._is_shown = False
            Adw.ApplicationWindow.hide(self)
            return

        # Disable keyboard grab immediately when starting to slide out
        Gtk4LayerShell.set_keyboard_mode(
            self, Gtk4LayerShell.KeyboardMode.NONE
        )

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
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _load_data(self) -> None:
        """Load chats from cache first, then sync from server."""
        if not self._config.is_configured:
            return

        # Step 1: Load from cache immediately (on main thread for fast startup)
        cached_chats = self._cache.get_all_chats()
        cached_contacts = self._cache.get_all_contacts()  # Returns dict[str, str]

        if cached_chats:
            self._chats = cached_chats
            self._contacts = cached_contacts  # Already a dict
            self._update_chat_list()

        # Step 2: Also fetch from server to get any very recent chats not yet in cache
        # (but don't replace cache data - merge it)
        def fetch() -> None:
            async def _fetch() -> list[Chat]:
                client = BlueBubblesClient(
                    self._config.server_url,  # type: ignore
                    self._config.password,  # type: ignore
                )
                try:
                    await client.connect()
                    chats = await client.get_chats(limit=50)
                    return chats
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                server_chats = loop.run_until_complete(_fetch())

                def merge_and_update() -> bool:
                    # Merge server chats with cached chats
                    # Server chats may have more recent messages
                    chats_by_guid = {c.guid: c for c in self._chats}
                    chats_by_identifier = {c.chat_identifier: c for c in self._chats}

                    for chat in server_chats:
                        existing = chats_by_guid.get(chat.guid) or chats_by_identifier.get(chat.chat_identifier)
                        if existing:
                            # Update if server has newer last message
                            if chat.last_message and existing.last_message:
                                if chat.last_message.date_created > existing.last_message.date_created:
                                    chats_by_guid[existing.guid] = chat
                            elif chat.last_message and not existing.last_message:
                                chats_by_guid[existing.guid] = chat
                        else:
                            # New chat not in cache
                            chats_by_guid[chat.guid] = chat

                    # Sort by last message date
                    all_chats = list(chats_by_guid.values())
                    all_chats.sort(
                        key=lambda c: c.last_message.date_created if c.last_message else 0,
                        reverse=True
                    )

                    self._chats = all_chats
                    self._update_chat_list()
                    return False

                GLib.idle_add(merge_and_update)
            except Exception:
                pass  # Server fetch failed, but we have cache data
            finally:
                loop.close()

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _connect_socket(self) -> None:
        """Connect to BlueBubbles Socket.IO for real-time updates."""
        if not self._config.is_configured:
            return

        def run_socket() -> None:
            async def _connect() -> None:
                self._socket = BlueBubblesSocket(
                    self._config.server_url,  # type: ignore
                    self._config.password,  # type: ignore
                )

                # Register callbacks
                self._socket.on_new_message(self._on_socket_new_message)
                self._socket.on_message_updated(self._on_socket_message_updated)
                self._socket.on_connected(self._on_socket_connected)
                self._socket.on_disconnected(self._on_socket_disconnected)

                try:
                    await self._socket.connect()
                    await self._socket.wait()
                except Exception:
                    pass  # Connection failed or disconnected

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_connect())
            except Exception:
                pass
            finally:
                loop.close()

        self._socket_thread = threading.Thread(target=run_socket, daemon=True)
        self._socket_thread.start()

    def _on_socket_connected(self) -> None:
        """Handle socket connection established."""
        pass  # Could show a status indicator if desired

    def _on_socket_disconnected(self) -> None:
        """Handle socket disconnection."""
        pass  # Could show a status indicator if desired

    def _on_socket_new_message(self, message: Message, chat_guid: str) -> None:
        """Handle new message from socket."""
        def update_ui() -> bool:
            # Skip reactions for now (keep it simple)
            if message.is_reaction:
                return False

            # Find the chat in our list (check both guid and chat_identifier)
            chat_index = -1
            updated_chat: Chat | None = None
            for i, chat in enumerate(self._chats):
                if chat.guid == chat_guid or chat.chat_identifier == chat_guid:
                    chat_index = i
                    # Update last message
                    chat_data = chat.model_dump(by_alias=True)
                    chat_data["lastMessage"] = message.model_dump(by_alias=True)
                    updated_chat = Chat(**chat_data)
                    break

            if updated_chat is not None and chat_index >= 0:
                # Move chat to top of list
                self._chats.pop(chat_index)
                self._chats.insert(0, updated_chat)
                # Rebuild chat list UI
                self._update_chat_list()
            else:
                # Fetch the chat from the API and add it
                self._fetch_and_add_chat(chat_guid, message)

            # If this chat is currently selected, add the message to the view
            if self._selected_chat and (self._selected_chat.guid == chat_guid or self._selected_chat.chat_identifier == chat_guid):
                # Check if message already exists
                if not any(m.guid == message.guid for m in self._messages):
                    self._messages.append(message)
                    row = self._create_message_row(message)
                    self._message_list.append(row)
                    # Scroll to bottom
                    def scroll_to_bottom() -> bool:
                        adj = self._message_list.get_parent().get_vadjustment()  # type: ignore
                        if adj:
                            adj.set_value(adj.get_upper())
                        return False
                    GLib.timeout_add(50, scroll_to_bottom)

            return False

        GLib.idle_add(update_ui)

    def _fetch_and_add_chat(self, chat_guid: str, message: Message) -> None:
        """Fetch a chat from the API and add it to the top of the list."""
        def fetch() -> None:
            async def _fetch() -> Chat | None:
                client = BlueBubblesClient(
                    self._config.server_url,  # type: ignore
                    self._config.password,  # type: ignore
                )
                try:
                    await client.connect()
                    chat = await client.get_chat(chat_guid)
                    return chat
                except Exception:
                    return None
                finally:
                    await client.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                chat = loop.run_until_complete(_fetch())
                if chat:
                    def add_to_ui() -> bool:
                        # Update last message
                        chat_data = chat.model_dump(by_alias=True)
                        chat_data["lastMessage"] = message.model_dump(by_alias=True)
                        updated_chat = Chat(**chat_data)
                        # Add to top of list
                        self._chats.insert(0, updated_chat)
                        self._update_chat_list()
                        return False
                    GLib.idle_add(add_to_ui)
            finally:
                loop.close()

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

    def _on_socket_message_updated(self, message: Message, chat_guid: str) -> None:
        """Handle message update from socket (e.g., read receipts)."""
        def update_ui() -> bool:
            # Update the message in our list if it exists
            if self._selected_chat and (self._selected_chat.guid == chat_guid or self._selected_chat.chat_identifier == chat_guid):
                for i, msg in enumerate(self._messages):
                    if msg.guid == message.guid:
                        self._messages[i] = message
                        break
            return False

        GLib.idle_add(update_ui)

    def _update_chat_list(self) -> None:
        """Update the chat list."""
        # Clear existing
        while True:
            row = self._chat_list.get_row_at_index(0)
            if row is None:
                break
            self._chat_list.remove(row)

        # Only display top 50 chats for performance
        for chat in self._chats[:50]:
            row = self._create_chat_row(chat)
            self._chat_list.append(row)

    def _create_chat_row(self, chat: Chat) -> Gtk.ListBoxRow:
        """Create a compact chat row."""
        row = Gtk.ListBoxRow()
        row.chat = chat  # type: ignore

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

        row.set_child(box)
        return row

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

        # NOTE: Don't call grab_focus() - it causes text entry issues
        # The user can click on the entry to focus it

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
            except Exception:
                pass
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

    # Sender colors for message bubbles (same as main app)
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

    def _get_sender_color(self, address: str) -> str:
        """Get a consistent color for a sender based on their address."""
        color_index = hash(address) % len(self.SENDER_COLORS)
        return self.SENDER_COLORS[color_index]

    def _get_sender_name(self, msg: Message) -> str:
        """Get display name for message sender."""
        if msg.handle:
            return self._get_display_name(msg.handle.address)
        return "Unknown"

    def _create_message_row(self, msg: Message) -> Gtk.ListBoxRow:
        """Create a message bubble row (matching main app style)."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.set_margin_top(2)
        container.set_margin_bottom(2)

        outer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        is_group = self._selected_chat and self._selected_chat.is_group

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        if msg.is_from_me:
            # Sent message - blue/right aligned
            bubble.set_margin_start(60)
            bubble.set_margin_end(12)
            bubble.set_halign(Gtk.Align.END)
            outer_box.set_halign(Gtk.Align.END)

            # Apply iMessage blue style
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
            bubble.set_margin_end(60)
            bubble.set_halign(Gtk.Align.START)
            outer_box.set_halign(Gtk.Align.START)

            sender_address = self._get_sender_name(msg)
            sender_color = self._get_sender_color(sender_address)

            # Show sender name in group chats
            if is_group:
                sender_label = Gtk.Label(label=sender_address, xalign=0)
                sender_label.add_css_class("caption")
                sender_label.set_margin_bottom(2)
                name_css = Gtk.CssProvider()
                name_css.load_from_data(f"""
                    .sender-name-panel {{
                        color: darker({sender_color});
                        font-weight: 600;
                    }}
                """.encode())
                Gtk.StyleContext.add_provider_for_display(
                    self.get_display(),
                    name_css,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                sender_label.add_css_class("sender-name-panel")
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

        # Message text
        if msg.text:
            text_label = Gtk.Label(label=msg.text, xalign=0)
            text_label.set_wrap(True)
            text_label.set_wrap_mode(2)  # WORD_CHAR
            text_label.set_max_width_chars(30)
            text_label.set_selectable(True)
            bubble.append(text_label)
        elif msg.has_attachments:
            text_label = Gtk.Label(label="(attachment)", xalign=0)
            text_label.add_css_class("dim-label")
            bubble.append(text_label)

        # Time row
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        time_str = msg.date_created_dt.strftime("%I:%M %p")
        time_label = Gtk.Label(label=time_str)
        time_label.add_css_class("caption")

        if msg.is_from_me:
            time_label.add_css_class("status-label-sent")
            status_box.set_halign(Gtk.Align.END)
        else:
            status_box.set_halign(Gtk.Align.START)
            if not is_group:
                sender_name = self._get_sender_name(msg)
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

        row.set_child(container)
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
                except Exception:
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
