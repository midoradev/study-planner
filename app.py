from __future__ import annotations
import streamlit as st
import pandas as pd
from datetime import date, timedelta
from uuid import uuid4

from calendar_export import tasks_to_ics
from calendar_import import parse_ics_bytes
from models import AppState, Subject
from pdf_export import week_plan_to_pdf
from planner import (
    build_risk_list,
    compute_busy_minutes_by_day,
    generate_week_plan,
    reschedule_overdue,
)
from storage import migrate_repo_data_once
from profiles import (
    create_profile,
    delete_profile,
    list_profiles,
    load_profile,
    migrate_legacy_state,
    save_profile,
)


DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

st.set_page_config(page_title="Study Planner", page_icon="📚", layout="wide")


def _ensure_session_state() -> list[str]:
    migrate_repo_data_once()
    migrate_legacy_state()
    profiles = list_profiles()
    if not profiles:
        create_profile("default")
        profiles = list_profiles()

    if "profile_name" not in st.session_state:
        st.session_state.profile_name = profiles[0]

    if st.session_state.profile_name not in profiles:
        st.session_state.profile_name = profiles[0]

    if "state" not in st.session_state:
        st.session_state.state = load_profile(st.session_state.profile_name)

    return profiles


profiles = _ensure_session_state()
state: AppState = st.session_state.state
current_profile = st.session_state.profile_name
today = date.today()

st.title("Study Planner")
st.caption("Local-first planner with calendar import/export, profiles, and weekly views.")


def _switch_profile(name: str) -> None:
    st.session_state.profile_name = name
    st.session_state.state = load_profile(name)


with st.sidebar:
    st.header("Profile")
    profiles = list_profiles()
    selected_profile = st.selectbox(
        "Active profile",
        options=profiles,
        index=profiles.index(current_profile) if current_profile in profiles else 0,
    )
    if selected_profile != current_profile:
        _switch_profile(selected_profile)
        st.rerun()

    new_profile_name = st.text_input("New profile name", placeholder="e.g. Semester A")
    if st.button("Create profile"):
        try:
            new_state = create_profile(new_profile_name)
        except ValueError as e:
            st.error(str(e))
        else:
            st.success(f"Profile '{new_profile_name}' created.")
            st.session_state.profile_name = new_profile_name.strip()
            st.session_state.state = new_state
            st.rerun()

    delete_confirm = st.checkbox("Enable delete", key="delete_confirm")
    if st.button("Delete profile") and delete_confirm:
        if len(profiles) <= 1:
            st.warning("Cannot delete the last profile.")
        else:
            delete_profile(current_profile)
            remaining = list_profiles()
            _switch_profile(remaining[0])
            st.success("Profile deleted.")
            st.rerun()

    reset_confirm = st.checkbox("Confirm reset", key="reset_confirm")
    if st.button("Reset current profile (keep settings)"):
        if not reset_confirm:
            st.warning("Check confirm to reset.")
        else:
            state.subjects = []
            state.tasks = []
            state.events = []
            save_profile(current_profile, state)
            st.success("Profile reset. Settings kept.")
            st.rerun()

    st.divider()
    st.header("Settings")
    state.settings.minutes_per_day = st.slider(
        "Minutes per day", 15, 600, state.settings.minutes_per_day, 15)
    state.settings.rest_days = st.multiselect(
        "Rest days (0=Mon)",
        options=list(range(7)),
        format_func=lambda x: DAY_LABELS[x],
        default=state.settings.rest_days,
    )
    chunk_options = [25, 45, 60]
    chunk_idx = chunk_options.index(state.settings.chunk_minutes) if state.settings.chunk_minutes in chunk_options else 0
    state.settings.chunk_minutes = st.selectbox(
        "Chunk size (minutes)", chunk_options, index=chunk_idx)
    state.settings.daily_buffer_minutes = st.slider(
        "Daily buffer (minutes)", 0, 120, state.settings.daily_buffer_minutes, 5)
    start_hour, end_hour = st.slider(
        "Preferred study hours", 0, 23,
        (state.settings.preferred_start_hour, state.settings.preferred_end_hour))
    state.settings.preferred_start_hour = start_hour
    state.settings.preferred_end_hour = end_hour
    st.caption("Calendar busy time and buffer reduce daily study capacity automatically.")

    if st.button("Save profile", type="primary"):
        save_profile(current_profile, state)
        st.success("Saved.")

    if st.button("Reload profile"):
        _switch_profile(current_profile)
        st.rerun()

    st.caption("Data is stored locally on this device, outside the project folder.")


tab_subjects, tab_calendar, tab_plan, tab_progress = st.tabs(
    ["Subjects", "Calendar", "Plan", "Progress"]
)


with tab_subjects:
    st.subheader("Add subject")
    with st.form("add_subject_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
        with col1:
            name = st.text_input("Name", placeholder="Math")
        with col2:
            difficulty = st.selectbox("Difficulty", [1, 2, 3, 4, 5], index=2)
        with col3:
            est_hours = st.number_input(
                "Estimated hours", min_value=0.5, max_value=200.0, value=6.0, step=0.5)
        with col4:
            deadline = st.date_input("Deadline", value=today)
        notes = st.text_area("Notes (optional)", height=80)
        submitted = st.form_submit_button("Add subject", type="primary")
        if submitted:
            if not name.strip():
                st.error("Name is required.")
            else:
                state.subjects.append(Subject(
                    id=str(uuid4()),
                    name=name.strip(),
                    deadline=deadline,
                    difficulty=int(difficulty),
                    est_hours=float(est_hours),
                    notes=notes.strip(),
                ))
                save_profile(current_profile, state)
                st.success("Subject added.")
                st.rerun()

    st.divider()
    st.subheader("Current subjects")
    if not state.subjects:
        st.info("No subjects yet.")
    else:
        for s in state.subjects:
            cols = st.columns([3, 1, 1, 2, 3, 1])
            cols[0].write(f"**{s.name}**")
            cols[1].write(f"D: {s.difficulty}")
            cols[2].write(f"Hrs: {s.est_hours:g}")
            cols[3].write(f"Due: {s.deadline.isoformat()}")
            cols[4].write(s.notes if s.notes else "")
            if cols[5].button("Delete", key=f"del_{s.id}"):
                state.subjects = [x for x in state.subjects if x.id != s.id]
                state.tasks = [t for t in state.tasks if t.subject_id != s.id]
                save_profile(current_profile, state)
                st.rerun()


with tab_calendar:
    st.subheader("Import Apple Calendar (.ics)")
    uploaded = st.file_uploader("Upload .ics file", type=["ics"])
    parsed_events = []
    if uploaded:
        try:
            parsed_events = parse_ics_bytes(uploaded.read())
        except Exception as e:
            st.error(f"Could not read ICS file: {e}")
        else:
            if not parsed_events:
                st.warning("No events found in this file.")
            else:
                preview = [{
                    "Title": ev.title,
                    "Start": ev.start,
                    "End": ev.end,
                    "Duration (m)": int((ev.end - ev.start).total_seconds() // 60),
                } for ev in parsed_events]
                st.dataframe(preview, width="stretch", height=250)
                import_mode = st.radio(
                    "Import mode", ["Merge", "Replace"], horizontal=True, index=0)
                if st.button("Import events into profile", type="primary"):
                    if import_mode == "Replace":
                        state.events = parsed_events
                    else:
                        existing_keys = {(e.title, e.start, e.end): e for e in state.events}
                        for ev in parsed_events:
                            key = (ev.title, ev.start, ev.end)
                            if key not in existing_keys:
                                existing_keys[key] = ev
                        state.events = list(existing_keys.values())
                    save_profile(current_profile, state)
                    st.success("Events imported.")
                    st.rerun()

    st.divider()
    st.subheader("Current events")
    if not state.events:
        st.info("No calendar events stored. Import to reduce capacity from busy times.")
    else:
        events_view = [{
            "Title": ev.title,
            "Start": ev.start,
            "End": ev.end,
            "Duration (m)": int((ev.end - ev.start).total_seconds() // 60),
        } for ev in sorted(state.events, key=lambda x: x.start)]
        st.dataframe(events_view, width="stretch", height=250)
        if st.button("Clear events"):
            state.events = []
            save_profile(current_profile, state)
            st.success("Events cleared.")
            st.rerun()

        st.markdown("### Convert events to subjects")
        sorted_events = sorted(state.events, key=lambda x: x.start)
        option_labels = {
            f"{ev.title} — {ev.start.strftime('%Y-%m-%d %H:%M')}": ev.id for ev in sorted_events
        }
        selected_event_labels = st.multiselect(
            "Select events to convert",
            options=list(option_labels.keys()),
        )
        default_difficulty = st.selectbox("Default difficulty", [1, 2, 3, 4, 5], index=2)
        default_hours = st.number_input(
            "Estimated hours for each converted subject",
            min_value=0.5,
            max_value=200.0,
            value=2.0,
            step=0.5,
        )
        remove_converted = st.checkbox("Remove converted events from calendar list", value=True)
        if st.button("Convert to subjects"):
            if not selected_event_labels:
                st.warning("Select at least one event.")
            else:
                selected_ids = {option_labels[label] for label in selected_event_labels}
                for ev in sorted_events:
                    if ev.id not in selected_ids:
                        continue
                    state.subjects.append(Subject(
                        id=str(uuid4()),
                        name=ev.title,
                        deadline=ev.start.date(),
                        difficulty=int(default_difficulty),
                        est_hours=float(default_hours),
                        notes="Imported from calendar",
                    ))
                if remove_converted:
                    state.events = [ev for ev in sorted_events if ev.id not in selected_ids]
                save_profile(current_profile, state)
                st.success("Events converted to subjects.")
                st.rerun()


with tab_plan:
    st.subheader("Weekly planning")
    default_week_start = state.last_generated_on or today
    week_start = st.date_input("Week start", value=default_week_start)
    week_end = week_start + timedelta(days=6)

    busy_by_day = compute_busy_minutes_by_day(state.events, week_start, num_days=7)

    colA, colB, colC = st.columns(3)
    if colA.button("Reschedule overdue"):
        state.tasks = reschedule_overdue(
            state.tasks, state.settings, today, state.events)
        save_profile(current_profile, state)
        st.success("Overdue tasks moved forward.")
        st.rerun()

    if colB.button("Generate / Refresh plan", type="primary"):
        state.tasks = generate_week_plan(
            state.subjects, state.settings, week_start, state.tasks, state.events)
        state.last_generated_on = week_start
        save_profile(current_profile, state)
        st.success("Plan generated.")
        st.rerun()

    if colC.button("Save progress"):
        save_profile(current_profile, state)
        st.success("Progress saved.")

    st.markdown(f"Week: **{week_start.isoformat()} – {week_end.isoformat()}**")

    st.divider()
    st.subheader("Risk list")
    risk_items = build_risk_list(state.subjects, state.tasks, today)
    if not risk_items:
        st.info("No risky subjects right now.")
    else:
        badge = {"HIGH": "🔴 HIGH", "MED": "🟠 MED", "LOW": "🟢 LOW"}
        risk_rows = []
        for r in risk_items:
            risk_rows.append({
                "Subject": r["subject"],
                "Level": badge.get(r["level"], r["level"]),
                "Days left": r["days_left"],
                "Remaining (m)": r["remaining_minutes"],
                "Remaining (h)": r["remaining_hours"],
                "Suggested m today": r["suggested_today_minutes"],
                "Difficulty": r["difficulty"],
                "Deadline": r["deadline"],
            })
        st.dataframe(risk_rows, width="stretch", height=240)

    st.divider()
    st.subheader("Weekly plan table")
    week_tasks = [t for t in state.tasks if week_start <= t.day <= week_end]
    subject_options = ["All subjects"] + sorted({s.name for s in state.subjects})
    chosen_subject = st.selectbox("Filter by subject", subject_options)
    filtered_tasks = [
        t for t in week_tasks if (
            chosen_subject == "All subjects" or t.subject_name == chosen_subject)
    ]

    if not filtered_tasks:
        st.info("No tasks in this week for the selected filter.")
    else:
        table_rows = [{
            "id": t.id,
            "Date": t.day.isoformat(),
            "Day": t.day.strftime("%a"),
            "Subject": t.subject_name,
            "Minutes": t.minutes,
            "Done": t.done,
            "Notes": t.notes,
        } for t in sorted(filtered_tasks, key=lambda x: (x.day, x.subject_name.lower()))]

        df = pd.DataFrame(table_rows).set_index("id")
        edited = st.data_editor(
            df,
            hide_index=True,
            column_config={
                "Date": st.column_config.TextColumn("Date"),
                "Day": st.column_config.TextColumn("Day"),
                "Subject": st.column_config.TextColumn("Subject"),
                "Minutes": st.column_config.NumberColumn("Minutes", format="%d"),
                "Done": st.column_config.CheckboxColumn("Done"),
                "Notes": st.column_config.TextColumn("Notes", width="medium"),
            },
            disabled=["Date", "Day", "Subject", "Minutes"],
            width="stretch",
            key="week_table_editor",
        )

        edited_records = edited.reset_index().to_dict("records")
        updates = {row["id"]: row for row in edited_records}
        changed = False
        for task in state.tasks:
            if task.id in updates:
                row = updates[task.id]
                new_done = bool(row.get("Done"))
                new_notes = row.get("Notes") or ""
                if task.done != new_done or (task.notes or "") != new_notes:
                    task.done = new_done
                    task.notes = new_notes
                    changed = True
        if changed:
            save_profile(current_profile, state)

        week_total = sum(t.minutes for t in filtered_tasks)
        week_done = sum(t.minutes for t in filtered_tasks if t.done)
        week_remaining = week_total - week_done
        m1, m2, m3 = st.columns(3)
        m1.metric("Week planned (m)", week_total)
        m2.metric("Done (m)", week_done)
        m3.metric("Remaining (m)", week_remaining)

        day_totals = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            day_tasks = [t for t in filtered_tasks if t.day == d]
            planned = sum(t.minutes for t in day_tasks)
            done = sum(t.minutes for t in day_tasks if t.done)
            busy = busy_by_day.get(d, 0)
            capacity = max(0, state.settings.minutes_per_day -
                           busy - state.settings.daily_buffer_minutes)
            day_totals.append({
                "Date": d.strftime("%a %m/%d"),
                "Planned (m)": planned,
                "Done (m)": done,
                "Remaining (m)": planned - done,
                "Busy (m)": busy,
                "Capacity after busy (m)": capacity,
            })
        st.table(day_totals)

    st.divider()
    st.subheader("Export")
    if week_tasks:
        ics_bytes, ics_warnings = tasks_to_ics(
            week_tasks, week_start, state.settings, state.events)
        st.download_button(
            "Download ICS for this week",
            data=ics_bytes,
            file_name=f"study_plan_{week_start.isoformat()}.ics",
            mime="text/calendar",
        )
        if ics_warnings:
            st.warning(" | ".join(ics_warnings))

        pdf_bytes = week_plan_to_pdf(
            week_tasks, state.settings, week_start, risk_items)
        st.download_button(
            "Download PDF",
            data=pdf_bytes,
            file_name=f"study_plan_{week_start.isoformat()}.pdf",
            mime="application/pdf",
        )
    else:
        st.info("No tasks to export for this week.")


with tab_progress:
    st.subheader("Progress overview")
    total_minutes = sum(t.minutes for t in state.tasks)
    done_minutes = sum(t.minutes for t in state.tasks if t.done)
    remaining_minutes = total_minutes - done_minutes

    a, b, c = st.columns(3)
    a.metric("Total minutes planned", total_minutes)
    b.metric("Minutes completed", done_minutes)
    c.metric("Minutes remaining", remaining_minutes)

    st.divider()
    st.subheader("By subject")
    if not state.subjects:
        st.info("No subjects yet.")
    else:
        subject_rows = []
        for subj in state.subjects:
            subj_tasks = [t for t in state.tasks if t.subject_id == subj.id]
            total = sum(t.minutes for t in subj_tasks)
            done = sum(t.minutes for t in subj_tasks if t.done)
            remaining = total - done
            subject_rows.append({
                "Subject": subj.name,
                "Done (m)": done,
                "Total (m)": total,
                "Remaining (m)": remaining,
                "Deadline": subj.deadline.isoformat(),
            })
        st.dataframe(subject_rows, width="stretch")
