"""Utilidades de zona horaria para Perú (UTC-5).

Perú no usa horario de verano, así que UTC-5 es constante.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta


PERU_TZ = timezone(timedelta(hours=-5), name="PET")
PERU_OFFSET_HOURS = -5


def now_pe() -> datetime:
    """Devuelve la hora actual en Lima (PET, UTC-5)."""
    return datetime.now(PERU_TZ)


def now_pe_iso(timespec: str = "seconds") -> str:
    """ISO 8601 de la hora actual en Lima."""
    return now_pe().isoformat(timespec=timespec)


def to_pe(dt: datetime) -> datetime:
    """Convierte un datetime (naïve o aware) a hora de Lima."""
    if dt.tzinfo is None:
        # Asumimos que el naive está en UTC (típico de datetime.now() en servidores)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PERU_TZ)


def parse_to_pe(iso_str: str) -> datetime | None:
    """Parsea un string ISO 8601 y lo devuelve en hora de Lima."""
    if not iso_str:
        return None
    try:
        # Tolerar diferentes formatos
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return to_pe(dt)
    except Exception:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(iso_str)
            return to_pe(dt)
        except Exception:
            return None


_MESES_ES = {1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
             7: "jul", 8: "ago", 9: "set", 10: "oct", 11: "nov", 12: "dic"}


def fmt_pe(iso_str: str | None, with_tz: bool = True) -> str:
    """Formatea un timestamp ISO en hora Lima, ej: '27 abr 18:43 PET'."""
    if not iso_str:
        return "—"
    dt = parse_to_pe(iso_str)
    if dt is None:
        return iso_str[:16]
    suffix = " PET" if with_tz else ""
    return f"{dt.day:02d} {_MESES_ES.get(dt.month, '?')} {dt.strftime('%H:%M')}{suffix}"


def fmt_pe_full(iso_str: str | None) -> str:
    """Formatea con año: '27 abr 2026 18:43 PET'."""
    if not iso_str:
        return "—"
    dt = parse_to_pe(iso_str)
    if dt is None:
        return iso_str
    return f"{dt.day:02d} {_MESES_ES.get(dt.month, '?')} {dt.year} · {dt.strftime('%H:%M')} PET"


def hours_ago_pe(iso_str: str | None) -> float:
    """Cuántas horas han pasado entre el timestamp y ahora (Lima)."""
    if not iso_str:
        return float("inf")
    dt = parse_to_pe(iso_str)
    if dt is None:
        return float("inf")
    delta = now_pe() - dt
    return delta.total_seconds() / 3600.0
