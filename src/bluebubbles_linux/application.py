"""GTK Application for BlueBubbles Linux."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from . import __app_id__, __version__
from .api import BlueBubblesClient
from .utils.config import Config


class BlueBubblesApplication(Adw.Application):
    """Main application class."""

    def __init__(self) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.config = Config()
        self.client: BlueBubblesClient | None = None
        self._main_window: Gtk.ApplicationWindow | None = None

        # Set up async event loop integration
        self._loop = asyncio.new_event_loop()

    def do_activate(self) -> None:
        """Called when the application is activated."""
        if not self._main_window:
            if self.config.is_configured:
                self._main_window = self._create_main_window()
            else:
                self._main_window = self._create_setup_window()

        self._main_window.present()

    def do_startup(self) -> None:
        """Called when the application starts."""
        Adw.Application.do_startup(self)
        self._setup_actions()

    def _setup_actions(self) -> None:
        """Set up application actions."""
        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        # About action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        # Settings action
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self._on_settings)
        self.add_action(settings_action)
        self.set_accels_for_action("app.settings", ["<Control>comma"])

        # Open chat action (for notification clicks)
        open_chat_action = Gio.SimpleAction.new("open-chat", GLib.VariantType.new("s"))
        open_chat_action.connect("activate", self._on_open_chat)
        self.add_action(open_chat_action)

    def _on_open_chat(self, _action: Any, param: GLib.Variant) -> None:
        """Handle notification click to open a specific chat."""
        chat_guid = param.get_string()
        if self._main_window and hasattr(self._main_window, 'select_chat_by_guid'):
            self._main_window.select_chat_by_guid(chat_guid)  # type: ignore
        # Bring window to front
        if self._main_window:
            self._main_window.present()

    def show_message_notification(
        self,
        title: str,
        body: str,
        chat_guid: str | None = None,
        icon_name: str = "chat-message-new-symbolic",
    ) -> None:
        """
        Send a desktop notification for a new message.

        Uses freedesktop D-Bus notifications for compatibility with
        hyprpanel, mako, dunst, swaync, and other notification daemons.

        Args:
            title: Notification title (sender name)
            body: Notification body (message preview)
            chat_guid: Optional chat GUID to open when clicked
            icon_name: Icon name for the notification
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            # Build actions for click handling
            actions: list[str] = []
            if chat_guid:
                actions = ["default", "Open Chat"]

            # Build hints
            hints = GLib.Variant("a{sv}", {
                "urgency": GLib.Variant("y", 1),  # Normal urgency
                "category": GLib.Variant("s", "im.received"),
                "desktop-entry": GLib.Variant("s", "com.bluebubbles.linux"),
            })

            # Call org.freedesktop.Notifications.Notify
            result = bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "BlueBubbles",  # app_name
                        0,  # replaces_id
                        icon_name,  # app_icon
                        title,  # summary
                        body,  # body
                        actions,  # actions
                        hints,  # hints
                        5000,  # expire_timeout (ms)
                    ),
                ),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )

            notification_id = result.unpack()[0]

            # Listen for action invoked (for click handling)
            if chat_guid:
                self._setup_notification_action_handler(bus, notification_id, chat_guid)

        except Exception as e:
            print(f"Failed to send notification: {e}")
            # Fallback to Gio.Notification
            self._send_gio_notification(title, body, chat_guid, icon_name)

    def _setup_notification_action_handler(
        self, bus: Gio.DBusConnection, notification_id: int, chat_guid: str
    ) -> None:
        """Set up handler for notification click action."""
        def on_action_invoked(
            connection: Gio.DBusConnection,
            sender_name: str,
            object_path: str,
            interface_name: str,
            signal_name: str,
            parameters: GLib.Variant,
        ) -> None:
            notif_id, action_key = parameters.unpack()
            if notif_id == notification_id and action_key == "default":
                # Open the chat
                GLib.idle_add(lambda: self._on_open_chat(None, GLib.Variant.new_string(chat_guid)))

        bus.signal_subscribe(
            "org.freedesktop.Notifications",
            "org.freedesktop.Notifications",
            "ActionInvoked",
            "/org/freedesktop/Notifications",
            None,
            Gio.DBusSignalFlags.NONE,
            on_action_invoked,
        )

    def _send_gio_notification(
        self, title: str, body: str, chat_guid: str | None, icon_name: str
    ) -> None:
        """Fallback notification using Gio.Notification."""
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(Gio.ThemedIcon.new(icon_name))
        notification.set_priority(Gio.NotificationPriority.HIGH)
        if chat_guid:
            notification.set_default_action_and_target(
                "app.open-chat", GLib.Variant.new_string(chat_guid)
            )
        notification_id = f"message-{chat_guid}" if chat_guid else "message"
        Gio.Application.send_notification(self, notification_id, notification)

    def _create_setup_window(self) -> Adw.ApplicationWindow:
        """Create the initial setup window."""
        window = Adw.ApplicationWindow(application=self)
        window.set_title("BlueBubbles Setup")
        window.set_default_size(450, 400)

        # Main content
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_top(48)
        box.set_margin_bottom(48)
        box.set_margin_start(48)
        box.set_margin_end(48)

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        title = Gtk.Label(label="Welcome to BlueBubbles")
        title.add_css_class("title-1")
        header_box.append(title)

        subtitle = Gtk.Label(label="Connect to your BlueBubbles server to get started")
        subtitle.add_css_class("dim-label")
        header_box.append(subtitle)

        box.append(header_box)

        # Form
        form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Server URL
        url_group = Adw.PreferencesGroup()
        url_row = Adw.EntryRow(title="Server URL")
        url_row.set_text(self.config.server_url or "")
        url_group.add(url_row)
        form_box.append(url_group)

        # Password
        password_group = Adw.PreferencesGroup()
        password_row = Adw.PasswordEntryRow(title="Server Password")
        password_group.add(password_row)
        form_box.append(password_group)

        box.append(form_box)

        # Status label
        status_label = Gtk.Label(label="")
        status_label.add_css_class("dim-label")
        box.append(status_label)

        # Connect button
        connect_button = Gtk.Button(label="Connect")
        connect_button.add_css_class("suggested-action")
        connect_button.add_css_class("pill")
        connect_button.set_halign(Gtk.Align.CENTER)

        def on_connect_clicked(_: Any) -> None:
            server_url = url_row.get_text().strip()
            password = password_row.get_text()

            if not server_url:
                status_label.set_text("Please enter a server URL")
                status_label.add_css_class("error")
                return

            if not password:
                status_label.set_text("Please enter a password")
                status_label.add_css_class("error")
                return

            status_label.remove_css_class("error")
            status_label.set_text("Connecting...")
            connect_button.set_sensitive(False)

            # Test connection in background
            def test_connection() -> None:
                async def _test() -> tuple[bool, str]:
                    from .api.client import test_connection
                    return await test_connection(server_url, password)

                loop = asyncio.new_event_loop()
                success, message = loop.run_until_complete(_test())
                loop.close()

                def update_ui() -> bool:
                    connect_button.set_sensitive(True)
                    if success:
                        # Save configuration
                        self.config.server_url = server_url
                        self.config.password = password
                        status_label.set_text("Connected! Loading...")
                        # Switch to main window
                        GLib.timeout_add(500, self._switch_to_main_window)
                    else:
                        status_label.set_text(message)
                        status_label.add_css_class("error")
                    return False

                GLib.idle_add(update_ui)

            import threading
            thread = threading.Thread(target=test_connection, daemon=True)
            thread.start()

        connect_button.connect("clicked", on_connect_clicked)
        box.append(connect_button)

        # Clamp for responsive width
        clamp = Adw.Clamp(maximum_size=400, child=box)

        # Toolbar view
        toolbar_view = Adw.ToolbarView()
        header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(header_bar)
        toolbar_view.set_content(clamp)

        window.set_content(toolbar_view)
        return window

    def _switch_to_main_window(self) -> bool:
        """Switch from setup to main window."""
        if self._main_window:
            self._main_window.close()
        self._main_window = self._create_main_window()
        self._main_window.present()
        return False

    def _create_main_window(self) -> Adw.ApplicationWindow:
        """Create the main application window."""
        from .ui.main_window import MainWindow
        return MainWindow(application=self)

    def _on_about(self, _action: Any, _param: Any) -> None:
        """Show about dialog."""
        about = Adw.AboutWindow(
            transient_for=self._main_window,
            application_name="BlueBubbles",
            application_icon=__app_id__,
            developer_name="BlueBubbles Linux",
            version=__version__,
            website="https://github.com/BlueBubblesApp/bluebubbles-server",
            issue_url="https://github.com/BlueBubblesApp/bluebubbles-server/issues",
            license_type=Gtk.License.MIT_X11,
            developers=["Nick"],
            copyright="2024",
        )
        about.present()

    def _on_settings(self, _action: Any, _param: Any) -> None:
        """Show settings dialog."""
        from .state import Cache

        dialog = Adw.PreferencesDialog()
        dialog.set_title("Settings")

        # General page
        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")

        # Server info group
        server_group = Adw.PreferencesGroup()
        server_group.set_title("Server")
        server_group.set_description("Connected to your BlueBubbles server")

        server_row = Adw.ActionRow()
        server_row.set_title("Server URL")
        server_row.set_subtitle(self.config.server_url or "Not configured")
        server_group.add(server_row)

        page.add(server_group)

        # Data management group
        data_group = Adw.PreferencesGroup()
        data_group.set_title("Data Management")
        data_group.set_description("Clear cached data. The app will restart after wiping.")

        # Wipe Conversations button
        wipe_convos_row = Adw.ActionRow()
        wipe_convos_row.set_title("Wipe Conversations")
        wipe_convos_row.set_subtitle("Clear cached chats and messages")
        wipe_convos_button = Gtk.Button(label="Wipe")
        wipe_convos_button.add_css_class("destructive-action")
        wipe_convos_button.set_valign(Gtk.Align.CENTER)
        wipe_convos_button.connect("clicked", lambda _: self._confirm_wipe(
            dialog, "conversations", "This will clear all cached chats and messages."
        ))
        wipe_convos_row.add_suffix(wipe_convos_button)
        data_group.add(wipe_convos_row)

        # Wipe Contacts button
        wipe_contacts_row = Adw.ActionRow()
        wipe_contacts_row.set_title("Wipe Contacts")
        wipe_contacts_row.set_subtitle("Clear cached contact names")
        wipe_contacts_button = Gtk.Button(label="Wipe")
        wipe_contacts_button.add_css_class("destructive-action")
        wipe_contacts_button.set_valign(Gtk.Align.CENTER)
        wipe_contacts_button.connect("clicked", lambda _: self._confirm_wipe(
            dialog, "contacts", "This will clear all cached contact names."
        ))
        wipe_contacts_row.add_suffix(wipe_contacts_button)
        data_group.add(wipe_contacts_row)

        # Wipe All button
        wipe_all_row = Adw.ActionRow()
        wipe_all_row.set_title("Wipe All Data")
        wipe_all_row.set_subtitle("Clear all cached data including attachments")
        wipe_all_button = Gtk.Button(label="Wipe All")
        wipe_all_button.add_css_class("destructive-action")
        wipe_all_button.set_valign(Gtk.Align.CENTER)
        wipe_all_button.connect("clicked", lambda _: self._confirm_wipe(
            dialog, "all", "This will clear ALL cached data including conversations, contacts, and attachments."
        ))
        wipe_all_row.add_suffix(wipe_all_button)
        data_group.add(wipe_all_row)

        page.add(data_group)
        dialog.add(page)

        dialog.present(self._main_window)

    def _confirm_wipe(self, settings_dialog: Adw.PreferencesDialog, wipe_type: str, message: str) -> None:
        """Show confirmation dialog for wiping data."""
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Wipe {wipe_type.title()}?")
        dialog.set_body(f"{message}\n\nThe app will restart after wiping.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("wipe", "Wipe")
        dialog.set_response_appearance("wipe", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dialog: Adw.AlertDialog, response: str) -> None:
            if response == "wipe":
                self._do_wipe(wipe_type)
                settings_dialog.close()

        dialog.connect("response", on_response)
        dialog.present(self._main_window)

    def _do_wipe(self, wipe_type: str) -> None:
        """Perform the data wipe and restart the app."""
        from .state import Cache
        import shutil

        cache = Cache()

        if wipe_type == "conversations":
            # Wipe chats and messages only
            conn = cache._get_conn()
            conn.executescript("""
                DELETE FROM messages;
                DELETE FROM chats;
                DELETE FROM sync_state;
            """)
            conn.commit()
            print("Wiped conversations")

        elif wipe_type == "contacts":
            # Wipe contacts only
            conn = cache._get_conn()
            try:
                conn.execute("DELETE FROM contacts")
                conn.commit()
            except Exception:
                pass  # Table might not exist
            print("Wiped contacts")

        elif wipe_type == "all":
            # Wipe everything including attachments
            cache.clear_all()
            # Also delete attachment files
            from .utils.config import CONFIG_DIR
            attachments_dir = CONFIG_DIR / "attachments"
            if attachments_dir.exists():
                shutil.rmtree(attachments_dir)
            # Delete link preview cache
            from pathlib import Path
            link_cache = Path.home() / ".cache" / "bluebubbles-linux" / "link_previews.db"
            if link_cache.exists():
                link_cache.unlink()
            print("Wiped all data")

        cache.close()

        # Restart the application
        self._restart_app()

    def _restart_app(self) -> None:
        """Restart the application."""
        import os
        import subprocess

        # Get the command used to start this app
        argv = sys.argv.copy()

        # Close the current instance
        self.quit()

        # Start a new instance after a short delay
        # Use GLib to schedule this after quit processing
        def do_restart() -> bool:
            try:
                subprocess.Popen(argv, start_new_session=True)
            except Exception as e:
                print(f"Failed to restart: {e}")
            return False

        GLib.timeout_add(100, do_restart)

    def get_client(self) -> BlueBubblesClient | None:
        """Get or create the API client."""
        if not self.config.is_configured:
            return None

        if self.client is None:
            self.client = BlueBubblesClient(
                self.config.server_url,  # type: ignore
                self.config.password,  # type: ignore
            )
        return self.client

    def run_async(self, coro: Any) -> None:
        """Run an async coroutine from the GTK main loop."""
        def run_in_thread() -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

        import threading
        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
