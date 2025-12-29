# BlueBubbles Linux

A native GTK4/Libadwaita desktop client for [BlueBubbles](https://bluebubbles.app/) on Linux, with special support for Hyprland and other Wayland compositors.

## Features

- **Native GTK4 + Libadwaita UI** - Modern GNOME-style interface that integrates with your desktop
- **Real-time messaging** - WebSocket connection for instant message delivery
- **Desktop notifications** - Freedesktop notifications compatible with hyprpanel, mako, dunst, swaync
- **Reactions/Tapbacks** - Send and receive iMessage reactions (❤️ 👍 👎 😂 ‼️ ❓)
- **Message editing** - Edit your sent messages (requires Private API on server)
- **Attachments** - View images, videos, and other attachments inline
- **Link previews** - Rich previews for shared URLs
- **Contact integration** - Shows contact names from your server's address book
- **Layer Shell side panel** - Slide-in/out panel for Hyprland/Wayland with smooth animations
- **Waybar integration** - Show unread count in your status bar
- **Keyboard navigation** - Full vim-style keyboard support in the side panel

## Installation

### System Dependencies

**Arch Linux:**
```bash
sudo pacman -S gtk4 libadwaita python-gobject python-pipx gtk4-layer-shell
```

**Fedora:**
```bash
sudo dnf install gtk4 libadwaita python3-gobject pipx gtk4-layer-shell
```

**Ubuntu/Debian:**
```bash
sudo apt install libgtk-4-dev libadwaita-1-dev python3-gi pipx
# gtk4-layer-shell may need to be built from source
```

### Install from Source

**Using pipx (Recommended):**
```bash
git clone https://github.com/yourusername/bluebubbles-linux.git
cd bluebubbles-linux
pipx install -e .
```

**Using pip:**
```bash
git clone https://github.com/yourusername/bluebubbles-linux.git
cd bluebubbles-linux
pip install --user -e .
```

Make sure `~/.local/bin` is in your PATH.

## Usage

### Main Application

Launch the full desktop application:

```bash
bluebubbles
```

On first launch, you'll be prompted to enter your BlueBubbles server URL and password.

### Side Panel

The side panel is a lightweight overlay for quick messaging, designed for tiling window managers like Hyprland. It slides in and out smoothly from the screen edge.

```bash
# Start the panel (anchored to left by default)
bluebubbles-panel

# Start with specific position
bluebubbles-panel -p left    # Left edge (default)
bluebubbles-panel -p right   # Right edge
bluebubbles-panel -p top     # Top edge (horizontal)
bluebubbles-panel -p bottom  # Bottom edge (horizontal)

# Toggle visibility (starts panel if not running)
bluebubbles-panel -t
bluebubbles-panel --toggle

# Other commands
bluebubbles-panel --show      # Show the panel
bluebubbles-panel --hide      # Hide the panel
bluebubbles-panel --status    # Get panel status as JSON
```

#### Panel Features

- **Slide animations** - Smooth slide-in/out from screen edge (no fade)
- **Conversation view** - Click a chat to slide into the message view
- **Quick reply** - Type and send messages without opening the full app
- **Vim-style navigation** - Use `j`/`k` or arrow keys to navigate

#### Panel Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` / `k` | Move up in conversation list |
| `↓` / `j` | Move down in conversation list |
| `Enter` / `Space` | Open selected conversation |
| `Tab` | Go back to conversation list |
| `Escape` | Go back, or slide out panel if on list |

## Hyprland Integration

Add to `~/.config/hypr/hyprland.conf` (or your custom config):

```conf
# Toggle BlueBubbles side panel
bind = $mainMod, M, exec, bluebubbles-panel -t -p left
```

The panel uses gtk4-layer-shell to appear as an overlay that doesn't interfere with your tiling layout.

## Notification Integration

Notifications use the **freedesktop D-Bus notification spec**, making them compatible with:

- **hyprpanel** - Appears in notification center
- **mako** - Lightweight Wayland notification daemon
- **dunst** - Customizable notification daemon
- **swaync** - Sway notification center
- **Any freedesktop-compliant daemon**

Features:
- Click notification to open the conversation
- Category hint `im.received` for custom styling
- 5-second timeout (configurable by your daemon)

## Waybar Integration

The side panel outputs status information to `$XDG_RUNTIME_DIR/bluebubbles-waybar.json`.

### Waybar Config

Add to `~/.config/waybar/config`:

```json
{
    "modules-right": ["custom/bluebubbles"],

    "custom/bluebubbles": {
        "exec": "cat $XDG_RUNTIME_DIR/bluebubbles-waybar.json 2>/dev/null || echo '{}'",
        "return-type": "json",
        "interval": 5,
        "format": "{} {icon}",
        "format-icons": {
            "unread": "󰍡",
            "read": "󰍥"
        },
        "on-click": "bluebubbles-panel -t",
        "tooltip": true
    }
}
```

### Waybar Styling

Add to `~/.config/waybar/style.css`:

```css
#custom-bluebubbles {
    color: #89b4fa;
    padding: 0 10px;
}

#custom-bluebubbles.has-unread {
    color: #a6e3a1;
    animation: pulse 1s ease infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
}
```

## Hyprpanel Integration

Notifications automatically appear in hyprpanel's notification center. No additional configuration needed - just make sure the main `bluebubbles` app is running to receive messages.

For the panel toggle, you can add a button to hyprpanel that runs:
```bash
bluebubbles-panel -t
```

## IPC Commands

The panel accepts commands via Unix socket at `$XDG_RUNTIME_DIR/bluebubbles-panel.sock`:

```bash
# Using netcat
echo "toggle" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock
echo "show" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock
echo "hide" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock
echo "status" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock
```

## Configuration

Configuration is stored in `~/.config/bluebubbles/`:

- `config.json` - Server URL and settings
- Password is stored securely using the system keyring

### Data Storage

- `~/.local/share/bluebubbles/` - SQLite cache for messages and contacts
- `~/.cache/bluebubbles-linux/` - Attachment cache and link previews

### Settings

Access settings from the main application via `Ctrl+,` or the menu:

- **Wipe Conversations** - Clear cached chats and messages
- **Wipe Contacts** - Clear cached contact names
- **Wipe All Data** - Clear everything including attachments

## Main App Features

### Conversations
- View all your iMessage/SMS conversations
- Start new conversations with contact autocomplete
- Group chat support
- Conversations sorted by most recent message

### Messages
- Send and receive text messages in real-time
- View message status (Sending, Sent, Delivered, Read)
- Right-click messages to react with tapbacks
- Right-click your own messages to edit (inline editing)
- View attachments inline (images, videos)
- Link previews with thumbnails

### Real-time Updates
- Instant message delivery via WebSocket
- Typing indicators
- Read receipts
- Desktop notifications with click-to-open

## Requirements

- Python 3.11+
- GTK 4.0+
- Libadwaita 1.0+
- A running [BlueBubbles Server](https://bluebubbles.app/) on macOS

### Optional
- gtk4-layer-shell (for Wayland layer shell panel support)

## Development

```bash
# Clone the repository
git clone https://github.com/yourusername/bluebubbles-linux.git
cd bluebubbles-linux

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode with dev dependencies
pip install -e ".[dev]"

# Run linting
ruff check src/

# Run type checking
mypy src/

# Run tests
pytest
```

## Troubleshooting

### Panel appears as regular window instead of overlay

If you see this warning:
```
Failed to initialize layer surface, GTK4 Layer Shell may have been linked after libwayland.
```

The panel automatically tries to fix this by re-launching with `LD_PRELOAD`. If it still doesn't work:

```bash
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so bluebubbles-panel
```

### Messages stuck on "Sending..."

This can happen if the server takes a while to respond. The message is likely sent - check your other devices. The status should update on the next sync.

### Contact names not showing

Make sure your BlueBubbles server has access to Contacts on macOS. You can also try "Wipe Contacts" in settings to force a refresh.

### Panel not responding to toggle

Check if the panel process is running:
```bash
bluebubbles-panel --status
```

If it returns an error, the panel isn't running. Start it with:
```bash
bluebubbles-panel
```

### Notifications not appearing

Make sure you have a notification daemon running (hyprpanel, mako, dunst, etc.). Test with:
```bash
notify-send "Test" "This is a test notification"
```

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.

## Credits

- [BlueBubbles](https://bluebubbles.app/) - The amazing server that makes this possible
- [GTK](https://gtk.org/) and [Libadwaita](https://gnome.pages.gitlab.gnome.org/libadwaita/) - UI toolkit
- [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) - Wayland layer shell support

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.
