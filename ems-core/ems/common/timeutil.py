"""Zeit: intern immer UTC (Anforderungen Kap. 3); Geräte liefern teils Ortszeit."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_local(value: str, fmt: str, tz: str) -> datetime | None:
    """Ortszeit-String (z. B. Controme "20.10.2026 11:56") -> UTC. Leer/ungültig -> None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    except ValueError:
        return None
