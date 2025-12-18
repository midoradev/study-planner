from __future__ import annotations
from datetime import date, timedelta
from io import BytesIO
from typing import List
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from models import Task, Settings


def week_plan_to_pdf(
    tasks: List[Task],
    settings: Settings,
    week_start: date,
    risk_items: List[dict],
) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    elems = []

    week_end = week_start + timedelta(days=6)
    elems.append(Paragraph(f"Study Plan: {week_start.isoformat()} - {week_end.isoformat()}", styles["Title"]))
    elems.append(Spacer(1, 10))
    elems.append(Paragraph(
        f"Minutes/day: {settings.minutes_per_day} | Rest days: {', '.join(map(str, settings.rest_days)) or 'None'} "
        f"| Chunk: {settings.chunk_minutes} | Buffer: {settings.daily_buffer_minutes}m",
        styles["Normal"],
    ))
    elems.append(Paragraph(
        f"Preferred window: {settings.preferred_start_hour}:00 - {settings.preferred_end_hour}:00",
        styles["Normal"],
    ))
    elems.append(Spacer(1, 12))

    if risk_items:
        elems.append(Paragraph("Risk list", styles["Heading3"]))
        risk_table_data = [["Subject", "Deadline", "Days left", "Remaining (m)", "Difficulty", "Level"]]
        for r in risk_items:
            risk_table_data.append([
                r["subject"],
                r["deadline"].isoformat(),
                str(r["days_left"]),
                str(r["remaining_minutes"]),
                str(r["difficulty"]),
                r["level"],
            ])
        risk_table = Table(risk_table_data, hAlign="LEFT")
        risk_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ALIGN", (2, 1), (3, -1), "RIGHT"),
        ]))
        elems.append(risk_table)
        elems.append(Spacer(1, 12))

    # Tasks by day
    by_day: dict[date, List[Task]] = {}
    for t in tasks:
        by_day.setdefault(t.day, []).append(t)

    for day in sorted(by_day.keys()):
        elems.append(Paragraph(day.strftime("%A, %Y-%m-%d"), styles["Heading3"]))
        day_tasks = sorted(by_day[day], key=lambda x: x.subject_name.lower())
        table_data = [["Subject", "Minutes", "Done", "Notes"]]
        total = 0
        for task in day_tasks:
            total += task.minutes
            table_data.append([
                task.subject_name,
                str(task.minutes),
                "Yes" if task.done else "No",
                task.notes or "",
            ])
        table_data.append(["Total", str(total), "", ""])

        table = Table(table_data, hAlign="LEFT", colWidths=[150, 60, 50, 200])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
            ("ALIGN", (1, 1), (2, -1), "RIGHT"),
        ]))
        elems.append(table)
        elems.append(Spacer(1, 8))

    doc.build(elems)
    return buf.getvalue()
