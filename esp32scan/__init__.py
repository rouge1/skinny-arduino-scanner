"""Host-side tools for the esp32-ai WiFi + Bluetooth scanner firmware."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "captures.db"
