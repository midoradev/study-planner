from __future__ import annotations
from datetime import datetime, date
from typing import List
from uuid import uuid4
from icalendar import Calendar
from models import Event


def _normalize_to_datetime(value) -> datetime | None:
    dt_value = getattr(value, "dt", value)

    if isinstance(dt_value, date) and not isinstance(dt_value, datetime):
        dt_value = datetime.combine(dt_value, datetime.min.time())

    if isinstance(dt_value, datetime):
        if dt_value.tzinfo:
            dt_value = dt_value.astimezone().replace(tzinfo=None)
        return dt_value
    return None


def parse_ics_bytes(data: bytes) -> List[Event]:
    cal = Calendar.from_ical(data)
    out: List[Event] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", "Untitled"))
        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")

        if not dtstart or not dtend:
            continue

        start_dt = _normalize_to_datetime(dtstart)
        end_dt = _normalize_to_datetime(dtend)

        if not start_dt or not end_dt or end_dt <= start_dt:
            continue

        out.append(Event(
            id=str(uuid4()),
            title=summary,
            start=start_dt,
            end=end_dt,
        ))

    return sorted(out, key=lambda x: x.start)
