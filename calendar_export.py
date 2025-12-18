from __future__ import annotations
from datetime import date, datetime, time, timedelta
from typing import List, Tuple
from zoneinfo import ZoneInfo
from uuid import uuid4
from icalendar import Calendar, Event as IcsEvent
from models import Task, Settings, Event
from planner import compute_busy_minutes_by_day


def _get_timezone() -> ZoneInfo:
    try:
        local = datetime.now().astimezone().tzinfo
        if isinstance(local, ZoneInfo):
            return local
    except Exception:
        pass
    try:
        return ZoneInfo("Asia/Bangkok")
    except Exception:
        # Fallback to a fixed offset; DST safety may be reduced but not broken
        return ZoneInfo("Asia/Bangkok")


def tasks_to_ics(
    tasks: List[Task],
    week_start: date,
    settings: Settings,
    events: List[Event] | None = None,
) -> Tuple[bytes, List[str]]:
    cal = Calendar()
    cal.add("PRODID", "-//Study Planner//Local//")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "Study Plan")

    warnings: List[str] = []
    tz = _get_timezone()
    start_hour = settings.preferred_start_hour
    end_hour = settings.preferred_end_hour
    if end_hour <= start_hour:
        end_hour = min(23, start_hour + 1)

    if not tasks:
        return cal.to_ical(), warnings

    start_day = min(week_start, min(t.day for t in tasks))
    end_day = max(week_start + timedelta(days=6), max(t.day for t in tasks))
    window_days = (end_day - start_day).days + 1

    events = events or []
    busy_by_day = compute_busy_minutes_by_day(events, start_day, num_days=window_days)

    day_info: dict[date, dict] = {}
    window_minutes = max(0, (end_hour - start_hour) * 60)
    for i in range(window_days):
        d = start_day + timedelta(days=i)
        window_start = datetime.combine(d, time(hour=start_hour, minute=0), tzinfo=tz)
        window_end = datetime.combine(d, time(hour=end_hour, minute=0), tzinfo=tz)
        if window_end <= window_start:
            window_end = window_start + timedelta(hours=1)

        busy = busy_by_day.get(d, 0)
        if d.weekday() in settings.rest_days:
            effective_capacity = 0
        else:
            effective_capacity = max(0, settings.minutes_per_day - busy - settings.daily_buffer_minutes)
        available_today = min(window_minutes, effective_capacity)
        day_info[d] = {
            "start": window_start,
            "cursor": window_start,
            "end": window_end,
            "available": available_today,
        }

    # Adjust availability with busy minutes from events
    for i in range(window_days):
        d = start_day + timedelta(days=i)
        busy = busy_by_day.get(d, 0)
        if d.weekday() in settings.rest_days:
            cap = 0
        else:
            cap = max(0, settings.minutes_per_day - busy - settings.daily_buffer_minutes)
        window_start = day_info[d]["start"]
        window_end = day_info[d]["end"]
        window_minutes_local = int((window_end - window_start).total_seconds() // 60)
        day_info[d]["available"] = min(window_minutes_local, cap)
        if day_info[d]["cursor"] < window_start:
            day_info[d]["cursor"] = window_start

    pending_unscheduled = 0
    for task in sorted(tasks, key=lambda x: (x.day, x.subject_name.lower())):
        minutes_left = task.minutes
        day_pointer = task.day
        while minutes_left > 0 and day_pointer <= end_day:
            info = day_info.get(day_pointer)
            if not info or info["available"] <= 0:
                day_pointer = day_pointer + timedelta(days=1)
                continue

            potential = min(minutes_left, info["available"])
            start_time = info["cursor"]
            end_time = min(info["end"], start_time + timedelta(minutes=potential))
            actual_minutes = int((end_time - start_time).total_seconds() // 60)
            if actual_minutes <= 0:
                day_pointer = day_pointer + timedelta(days=1)
                continue

            event = IcsEvent()
            uid = f"{task.id}-{start_time.strftime('%Y%m%dT%H%M')}"
            event.add("uid", f"{uid}@study-planner")
            event.add("summary", f"Study: {task.subject_name}")
            event.add("dtstart", start_time)
            event.add("dtend", end_time)
            planned_desc = f"{task.minutes} minutes planned"
            if day_pointer != task.day:
                planned_desc += f" (moved from {task.day.isoformat()})"
            event.add("description", planned_desc + ".")
            cal.add_component(event)

            info["cursor"] = end_time
            info["available"] -= actual_minutes
            minutes_left -= actual_minutes

            if info["cursor"] >= info["end"] or info["available"] <= 0:
                day_pointer = day_pointer + timedelta(days=1)

        if minutes_left > 0:
            pending_unscheduled += minutes_left
            warnings.append(
                f"{task.subject_name} had {minutes_left} minutes that could not be placed within the planning window."
            )

    if pending_unscheduled > 0:
        overflow_event = IcsEvent()
        overflow_event.add("uid", f"unscheduled-{uuid4()}@study-planner")
        overflow_event.add("summary", f"Unscheduled Study ({pending_unscheduled} minutes)")
        overflow_event.add("dtstart", datetime.combine(end_day, time.min, tzinfo=tz))
        overflow_event.add("dtend", datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz))
        overflow_event.add("description", "No capacity remained in this window; please reschedule.")
        cal.add_component(overflow_event)

    return cal.to_ical(), warnings
