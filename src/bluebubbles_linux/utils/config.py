"""Configuration management for BlueBubbles Linux."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

APP_ID = "bluebubbles-linux"
CONFIG_DIR = Path.home() / ".config" / "bluebubbles-linux"
CONFIG_FILE = CONFIG_DIR / "config.json"
SECRETS_FILE = CONFIG_DIR / "secrets.json"  # Fallback when keyring unavailable


def _keyring_available() -> bool:
    """Check if a working keyring backend is available."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        backend = keyring.get_keyring()
        return not isinstance(backend, FailKeyring)
    except Exception:
        return False


class Config:
    """Manages application configuration with secure credential storage."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._secrets: dict[str, str] = {}
        self._use_keyring = _keyring_available()
        self._load()

    def _load(self) -> None:
        """Load configuration from disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # Load main config
        if CONFIG_FILE.exists():
            try:
                self._config = json.loads(CONFIG_FILE.read_text())
            except json.JSONDecodeError:
                self._config = {}
        else:
            self._config = {}

        # Load fallback secrets if not using keyring
        if not self._use_keyring and SECRETS_FILE.exists():
            try:
                self._secrets = json.loads(SECRETS_FILE.read_text())
            except json.JSONDecodeError:
                self._secrets = {}

    def _save(self) -> None:
        """Save configuration to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self._config, indent=2))

    def _save_secrets(self) -> None:
        """Save secrets to fallback file (when keyring unavailable)."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SECRETS_FILE.write_text(json.dumps(self._secrets, indent=2))
        # Set restrictive permissions
        os.chmod(SECRETS_FILE, 0o600)

    @property
    def server_url(self) -> str | None:
        """Get the BlueBubbles server URL."""
        return self._config.get("server_url")

    @server_url.setter
    def server_url(self, value: str) -> None:
        """Set the BlueBubbles server URL."""
        # Normalize URL - remove trailing slash
        value = value.rstrip("/")
        self._config["server_url"] = value
        self._save()

    @property
    def password(self) -> str | None:
        """Get the server password from secure storage."""
        if self._use_keyring:
            import keyring

            return keyring.get_password(APP_ID, "server_password")
        else:
            # Fallback: base64 encoded in local file (not truly secure, but better than plaintext)
            encoded = self._secrets.get("server_password")
            if encoded:
                try:
                    return base64.b64decode(encoded).decode("utf-8")
                except Exception:
                    return None
            return None

    @password.setter
    def password(self, value: str) -> None:
        """Store the server password."""
        if self._use_keyring:
            import keyring

            keyring.set_password(APP_ID, "server_password", value)
        else:
            # Fallback: base64 encode and store in file
            self._secrets["server_password"] = base64.b64encode(value.encode("utf-8")).decode(
                "ascii"
            )
            self._save_secrets()

    def delete_password(self) -> None:
        """Remove the stored password."""
        if self._use_keyring:
            import keyring

            try:
                keyring.delete_password(APP_ID, "server_password")
            except keyring.errors.PasswordDeleteError:
                pass
        else:
            self._secrets.pop("server_password", None)
            self._save_secrets()

    @property
    def is_configured(self) -> bool:
        """Check if the app has been configured with server details."""
        return bool(self.server_url and self.password)

    @property
    def using_secure_storage(self) -> bool:
        """Check if we're using secure keyring storage."""
        return self._use_keyring

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        self._config[key] = value
        self._save()
