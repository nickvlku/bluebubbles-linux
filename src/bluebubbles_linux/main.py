"""Entry point for BlueBubbles Linux."""

from __future__ import annotations

import os
import sys


def _ensure_layer_shell_preload() -> None:
    """Ensure gtk4-layer-shell is preloaded before wayland client."""
    # Check if we need to re-exec with LD_PRELOAD
    if "GTK4_LAYER_SHELL_PRELOADED" in os.environ:
        return  # Already preloaded

    # Common paths for libgtk4-layer-shell.so
    lib_paths = [
        "/usr/lib/libgtk4-layer-shell.so",
        "/usr/lib64/libgtk4-layer-shell.so",
        "/usr/local/lib/libgtk4-layer-shell.so",
    ]

    lib_path = None
    for path in lib_paths:
        if os.path.exists(path):
            lib_path = path
            break

    if lib_path:
        # Set LD_PRELOAD and re-exec
        current_preload = os.environ.get("LD_PRELOAD", "")
        if lib_path not in current_preload:
            if current_preload:
                os.environ["LD_PRELOAD"] = f"{lib_path}:{current_preload}"
            else:
                os.environ["LD_PRELOAD"] = lib_path
            os.environ["GTK4_LAYER_SHELL_PRELOADED"] = "1"
            os.execv(sys.executable, [sys.executable] + sys.argv)


def main() -> int:
    """Main entry point for the desktop application."""
    from .application import BlueBubblesApplication

    app = BlueBubblesApplication()
    return app.run(sys.argv)


def panel() -> int:
    """Entry point for the Hyprland side panel."""
    import argparse

    # Ensure gtk4-layer-shell is preloaded
    _ensure_layer_shell_preload()

    from .ui.side_panel import run_panel, send_ipc_command

    parser = argparse.ArgumentParser(description="BlueBubbles side panel")
    parser.add_argument(
        "-t", "--toggle",
        action="store_true",
        help="Toggle panel visibility (if already running)"
    )
    parser.add_argument(
        "-p", "--position",
        choices=["left", "right", "top", "bottom"],
        default="left",
        help="Panel position (default: left)"
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show the panel"
    )
    parser.add_argument(
        "--hide",
        action="store_true",
        help="Hide the panel"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print panel status as JSON"
    )
    args = parser.parse_args(sys.argv[1:])

    # Handle IPC commands
    if args.status:
        result = send_ipc_command("status")
        if result:
            print(result)
            return 0
        print('{"error": "panel not running"}')
        return 1

    if args.show:
        result = send_ipc_command("show")
        if result == "ok":
            return 0
        # Fall through to start panel if not running

    if args.hide:
        result = send_ipc_command("hide")
        return 0 if result == "ok" else 1

    return run_panel(toggle=args.toggle, position=args.position)


if __name__ == "__main__":
    sys.exit(main())
