# BlueBubbles Linux Desktop App - Implementation Plan

## Overview
A GTK4 + Python desktop app for Linux/Hyprland that connects to a BlueBubbles server, featuring a Layer Shell side panel for quick messaging.

## Technology Stack
- **Language**: Python 3.11+
- **GUI Framework**: GTK4 + Libadwaita (modern GNOME styling)
- **Layer Shell**: gtk4-layer-shell (for Hyprland side panel)
- **HTTP Client**: httpx (async support)
- **WebSocket**: python-socketio (for real-time updates)
- **Notifications**: libnotify via PyGObject
- **Build/Package**: Meson + PyPI dependencies

## Architecture

```
bluebubbles-linux/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point, app initialization
│   ├── application.py          # GtkApplication subclass
│   ├── api/
│   │   ├── __init__.py
│   │   ├── client.py           # REST API client (httpx)
│   │   ├── socket_client.py    # Socket.IO client for real-time
│   │   ├── models.py           # Pydantic models for API responses
│   │   └── endpoints.py        # API endpoint definitions
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── main_window.py      # Main application window
│   │   ├── conversation_list.py # Sidebar with chat list
│   │   ├── message_view.py     # Message display area
│   │   ├── compose_box.py      # Message input with attachments
│   │   ├── side_panel.py       # Layer Shell quick-reply panel
│   │   └── widgets/
│   │       ├── message_bubble.py
│   │       ├── attachment_widget.py
│   │       ├── typing_indicator.py
│   │       └── reaction_picker.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── notification_service.py  # Desktop notifications
│   │   ├── message_service.py       # Message send/receive logic
│   │   └── attachment_service.py    # File handling
│   ├── state/
│   │   ├── __init__.py
│   │   ├── app_state.py        # Global state management
│   │   └── cache.py            # Local message/attachment cache
│   └── utils/
│       ├── __init__.py
│       ├── config.py           # Settings management
│       └── keyring.py          # Secure password storage
├── data/
│   ├── com.github.bluebubbles-linux.desktop
│   ├── com.github.bluebubbles-linux.gschema.xml
│   └── icons/
├── tests/
├── meson.build
├── pyproject.toml
└── README.md
```

## Implementation Phases

### Phase 1: Project Setup & API Client
**Files to create:**
- `pyproject.toml` - Dependencies and project metadata
- `src/main.py` - Entry point
- `src/application.py` - GTK Application class
- `src/api/client.py` - BlueBubbles REST client
- `src/api/models.py` - Data models
- `src/utils/config.py` - Configuration management

**Key tasks:**
1. Set up Python project with pyproject.toml
2. Implement BlueBubbles API client with authentication
3. Create Pydantic models for Chat, Message, Handle, Attachment
4. Add configuration for server URL and password (using keyring)
5. Test API connectivity with `/api/v1/ping`

### Phase 2: Core UI - Main Window
**Files to create:**
- `src/ui/main_window.py` - Main window with Adwaita styling
- `src/ui/conversation_list.py` - Chat list sidebar
- `src/ui/message_view.py` - Message display
- `src/ui/compose_box.py` - Message input

**Key tasks:**
1. Create main window with split-pane layout (sidebar + content)
2. Implement conversation list with search
3. Build message view with scrollable history
4. Add compose box with send button
5. Wire up API client to fetch and display chats/messages

### Phase 3: Real-time & Notifications
**Files to create:**
- `src/api/socket_client.py` - Socket.IO integration
- `src/services/notification_service.py` - Desktop notifications
- `src/services/message_service.py` - Message handling

**Key tasks:**
1. Connect to BlueBubbles Socket.IO for real-time events
2. Handle incoming message events and update UI
3. Implement desktop notifications with libnotify
4. Add typing indicators (send and receive)
5. Handle read receipts

### Phase 4: Attachments & Reactions
**Files to create:**
- `src/ui/widgets/attachment_widget.py` - Image/file display
- `src/ui/widgets/reaction_picker.py` - Tapback picker
- `src/services/attachment_service.py` - File handling

**Key tasks:**
1. Display inline images in messages
2. Add file attachment picker in compose box
3. Implement reaction/tapback UI and API calls
4. Handle attachment downloads and caching

### Phase 5: Hyprland Layer Shell Panel
**Files to create:**
- `src/ui/side_panel.py` - Layer Shell panel window

**Key tasks:**
1. Create Layer Shell window using gtk4-layer-shell
2. Configure anchoring (right edge, partial height)
3. Add compact conversation list
4. Implement quick-reply compose box
5. Add toggle keybind integration (via Hyprland config)

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/ping` | GET | Connection test |
| `/api/v1/server/info` | GET | Server details |
| `/api/v1/chat` | GET | List conversations |
| `/api/v1/chat/:guid/message` | GET | Chat messages |
| `/api/v1/message/text` | POST | Send text message |
| `/api/v1/message/:guid/react` | POST | Send reaction |
| `/api/v1/attachment/:guid` | GET | Download attachment |
| `/api/v1/attachment/:guid/upload` | POST | Upload attachment |
| `/api/v1/chat/:guid/typing` | POST | Send typing indicator |

## Socket.IO Events

**Subscribe to:**
- `new-message` - Incoming messages
- `updated-message` - Delivery/read status
- `typing-indicator` - Contact typing
- `group-name-change` - Group chat updates
- `participant-added/removed` - Group membership

## Dependencies

```toml
[project]
dependencies = [
    "pygobject>=3.46",
    "httpx>=0.27",
    "python-socketio[asyncio]>=5.11",
    "pydantic>=2.5",
    "keyring>=25.0",
]
```

**System packages required (Arch):**
```bash
sudo pacman -S gtk4 libadwaita python-gobject gtk4-layer-shell
```

## Hyprland Integration

Add to `~/.config/hypr/hyprland.conf`:
```conf
# Toggle BlueBubbles side panel
bind = SUPER, M, exec, bluebubbles-panel --toggle

# Window rules for Layer Shell panel (if not using layer shell)
windowrulev2 = float, class:^(com.bluebubbles.Panel)$
windowrulev2 = pin, class:^(com.bluebubbles.Panel)$
windowrulev2 = move 100%-390 60, class:^(com.bluebubbles.Panel)$
windowrulev2 = size 380 800, class:^(com.bluebubbles.Panel)$
```

## Waybar Integration

The side panel outputs a JSON file for waybar integration at `$XDG_RUNTIME_DIR/bluebubbles-waybar.json`.

Add to `~/.config/waybar/config`:
```json
{
    "modules-right": ["custom/bluebubbles", ...],

    "custom/bluebubbles": {
        "exec": "cat $XDG_RUNTIME_DIR/bluebubbles-waybar.json 2>/dev/null || echo '{}'",
        "return-type": "json",
        "interval": 5,
        "format": "{} {icon}",
        "format-icons": {
            "unread": "󰍡",
            "read": "󰍥"
        },
        "on-click": "bluebubbles-panel --toggle",
        "tooltip": true
    }
}
```

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

### IPC Commands

The panel supports IPC commands via Unix socket at `$XDG_RUNTIME_DIR/bluebubbles-panel.sock`:

```bash
# Toggle panel visibility
echo "toggle" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock

# Show panel
echo "show" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock

# Hide panel
echo "hide" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock

# Get status
echo "status" | nc -U $XDG_RUNTIME_DIR/bluebubbles-panel.sock
```

## First Milestone
A working app that can:
1. Connect to BlueBubbles server
2. Display conversation list
3. Show message history for selected chat
4. Send and receive text messages in real-time
5. Show desktop notifications for new messages
