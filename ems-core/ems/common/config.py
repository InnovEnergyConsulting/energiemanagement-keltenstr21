"""Konfiguration aus Umgebungsvariablen und Geheimnisse aus Dateien (Compose: ./secrets)."""
import os
from pathlib import Path


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Umgebungsvariable {name} fehlt")
    return value


def secret(name: str) -> str:
    """Liest ein Geheimnis; der Wert wird nie geloggt."""
    path = Path(os.environ.get("EMS_SECRETS_DIR", "/run/ems-secrets")) / name
    return path.read_text(encoding="utf-8").strip()


def mapping_dir() -> Path:
    return Path(os.environ.get("EMS_MAPPING_DIR", "/app/mapping"))
