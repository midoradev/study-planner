from __future__ import annotations
from datetime import date, datetime, timedelta, time
from typing import Dict, List
from uuid import uuid4
from models import Subject, Task, Settings, Event


def _days_left(today: date, deadline: date) -> int:
    return max(1, (deadline - today).days)


def _priority(today: date, s: Subject) -> float:
    # higher = more urgent
    return (s.difficulty * 10.0) / _days_left(today, s.deadline)


def compute_busy_minutes_by_day(
    events: List[Event],
    start_date: date,
    num_days: int = 7,
) -> Dict[date, int]:
    busy: Dict[date, int] = {start_date + timedelta(days=i): 0 for i in range(num_days)}
    window_dates = list(busy.keys())
    for ev in events:
        start = ev.start
        end = ev.end
        if end <= start:
            continue
        for d in window_dates:
            day_start = datetime.combine(d, time.min)
            day_end = day_start + timedelta(days=1)
            overlap_start = max(start, day_start)
            overlap_end = min(end, day_end)
            if overlap_end > overlap_start:
                minutes = int((overlap_end - overlap_start).total_seconds() // 60)
                busy[d] += minutes
    return busy


def _available_minutes_for_day(
    d: date,
    settings: Settings,
    busy_by_day: Dict[date, int],
    planned_minutes: Dict[date, int] | None = None,
) -> int:
    if d.weekday() in settings.rest_days:
        return 0
    busy = busy_by_day.get(d, 0)
    planned = 0 if planned_minutes is None else planned_minutes.get(d, 0)
    base = max(0, settings.minutes_per_day - busy - settings.daily_buffer_minutes)
    return max(0, base - planned)


def generate_week_plan(
    subjects: List[Subject],
    settings: Settings,
    today: date,
    existing_tasks: List[Task],
    events: List[Event] | None = None,
) -> List[Task]:
    events = events or []
    days = [today + timedelta(days=i) for i in range(7)]
    busy_by_day = compute_busy_minutes_by_day(events, today, num_days=7)

    day_capacity: Dict[date, int] = {}
    for d in days:
        if d.weekday() in settings.rest_days:
            base = 0
        else:
            base = settings.minutes_per_day
        busy = busy_by_day.get(d, 0)
        day_capacity[d] = max(0, base - busy - settings.daily_buffer_minutes)

    # Reduce capacity by already planned, unfinished tasks in the window
    for t in existing_tasks:
        if not t.done and t.day in day_capacity:
            day_capacity[t.day] = max(0, day_capacity[t.day] - t.minutes)

    # Track how much time still needed per subject
    targets = {s.id: int(round(s.est_hours * 60)) for s in subjects}
    allocated = {sid: 0 for sid in targets}
    for t in existing_tasks:
        if t.subject_id in allocated:
            allocated[t.subject_id] += t.minutes

    remaining = {sid: max(0, targets[sid] - allocated.get(sid, 0))
                 for sid in targets}

    chunk = settings.chunk_minutes if settings.chunk_minutes in (25, 45, 60) else 25
    chunk = max(10, chunk)

    new_tasks: List[Task] = []
    for d in days:
        cap = day_capacity.get(d, 0)
        if cap <= 0:
            continue

        while cap >= 10:
            candidates = [s for s in subjects if remaining.get(s.id, 0) > 0]
            if not candidates:
                break

            candidates.sort(key=lambda s: _priority(today, s), reverse=True)
            s = candidates[0]
            give = min(chunk, cap, remaining[s.id])
            if give < 10:
                break

            new_tasks.append(Task(
                id=str(uuid4()),
                subject_id=s.id,
                subject_name=s.name,
                day=d,
                minutes=give,
                done=False,
                notes="",
            ))
            remaining[s.id] -= give
            cap -= give

    return existing_tasks + new_tasks


def reschedule_overdue(
    tasks: List[Task],
    settings: Settings,
    today: date,
    events: List[Event] | None = None,
) -> List[Task]:
    events = events or []
    overdue = [t for t in tasks if (not t.done and t.day < today)]
    keep = [t for t in tasks if not (not t.done and t.day < today)]

    horizon_days = 180
    busy_by_day = compute_busy_minutes_by_day(events, today, num_days=horizon_days)
    planned_minutes: Dict[date, int] = {}
    for t in keep:
        if not t.done:
            planned_minutes[t.day] = planned_minutes.get(t.day, 0) + t.minutes

    cursor = today
    for t in overdue:
        minutes_left = t.minutes
        attempts = 0
        while minutes_left > 0 and attempts < horizon_days:
            cap = _available_minutes_for_day(cursor, settings, busy_by_day, planned_minutes)
            if cap <= 0:
                cursor = cursor + timedelta(days=1)
                attempts += 1
                continue

            take = min(minutes_left, cap)
            new_task = Task(
                id=str(uuid4()),
                subject_id=t.subject_id,
                subject_name=t.subject_name,
                day=cursor,
                minutes=take,
                done=False,
                notes=t.notes,
            )
            keep.append(new_task)
            planned_minutes[cursor] = planned_minutes.get(cursor, 0) + take
            minutes_left -= take
            if cap - take <= 0:
                cursor = cursor + timedelta(days=1)
            attempts += 1

        if minutes_left > 0:
            overflow_day = cursor if cursor >= today else today
            keep.append(Task(
                id=str(uuid4()),
                subject_id=t.subject_id,
                subject_name=t.subject_name,
                day=overflow_day,
                minutes=minutes_left,
                done=False,
                notes=(t.notes or "") + " (overflow, please reschedule)",
            ))

    keep.sort(key=lambda x: (x.day, x.subject_name.lower()))
    return keep


def build_risk_list(
    subjects: List[Subject],
    tasks: List[Task],
    today: date,
    limit: int = 5,
) -> List[dict]:
    done_minutes: Dict[str, int] = {}
    planned_minutes: Dict[str, int] = {}
    for t in tasks:
        planned_minutes[t.subject_id] = planned_minutes.get(t.subject_id, 0) + t.minutes
        if t.done:
            done_minutes[t.subject_id] = done_minutes.get(t.subject_id, 0) + t.minutes

    risks = []
    for s in subjects:
        total_needed = int(round(s.est_hours * 60))
        remaining = max(0, total_needed - planned_minutes.get(s.id, 0))
        days_left = _days_left(today, s.deadline)
        urgency = 1 / days_left
        score = remaining * urgency * s.difficulty
        if score <= 0:
            continue
        suggested_today = int(round(remaining / max(1, days_left)))
        if score >= 500:
            level = "HIGH"
        elif score >= 200:
            level = "MED"
        else:
            level = "LOW"
        risks.append({
            "subject": s.name,
            "deadline": s.deadline,
            "days_left": days_left,
            "remaining_minutes": remaining,
            "remaining_hours": round(remaining / 60, 1),
            "suggested_today_minutes": suggested_today,
            "difficulty": s.difficulty,
            "score": score,
            "level": level,
        })

    risks.sort(key=lambda x: x["score"], reverse=True)
    return risks[:limit]
