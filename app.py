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
from storage import ensure_data_dir, migrate_repo_data_once
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
    ensure_data_dir()
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


def _switch_profile(name: str) -> None:
    st.session_state.profile_name = name
    st.session_state.state = load_profile(name)


def _coerce_date(value: object) -> date | None:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _queue_toast(message: str) -> None:
    st.session_state.toast_message = message


def _flush_toast() -> None:
    message = st.session_state.pop("toast_message", None)
    if message:
        st.toast(message)


def _render_first_run_checklist(state: AppState) -> None:
    st.subheader("Getting started")
    steps = [
        (
            "Add subjects",
            bool(state.subjects),
            "Add at least one subject to start planning.",
        ),
        (
            "Import calendar (optional)",
            bool(state.events),
            "Bring in busy times if you want smarter capacity.",
        ),
        (
            "Generate plan",
            bool(state.tasks),
            "Create your first weekly plan.",
        ),
    ]
    for label, done, helper in steps:
        icon = "✅" if done else "⬜"
        st.write(f"{icon} {label} — {helper}")


def render_setup(state: AppState) -> None:
    st.header("Setup")

    if not state.subjects:
        st.info("Start here to build your plan.")
        _render_first_run_checklist(state)
        st.divider()

    st.subheader("Add subject")
    with st.form("add_subject_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
        with col1:
            name = st.text_input("Name", placeholder="Math")
        with col2:
            difficulty = st.selectbox("Difficulty", [1, 2, 3, 4, 5], index=2)
        with col3:
            est_hours = st.number_input(
                "Estimated hours", min_value=0.5, max_value=200.0, value=6.0, step=0.5
            )
        with col4:
            deadline = st.date_input("Deadline", value=date.today())
        notes = st.text_area("Notes (optional)", height=80)
        submitted = st.form_submit_button("Add subject", type="primary")
        if submitted:
            if not name.strip():
                st.warning("Name is required.")
            else:
                state.subjects.append(
                    Subject(
                        id=str(uuid4()),
                        name=name.strip(),
                        deadline=deadline,
                        difficulty=int(difficulty),
                        est_hours=float(est_hours),
                        notes=notes.strip(),
                    )
                )
                save_profile(current_profile, state)
                st.toast("Subject added.")

    st.divider()
    st.subheader("Subjects manager")
    if not state.subjects:
        st.info("No subjects yet.")
        return

    rows = [
        {
            "Select": False,
            "id": s.id,
            "Name": s.name,
            "Deadline": s.deadline,
            "Difficulty": s.difficulty,
            "Est hours": s.est_hours,
            "Notes": s.notes or "",
        }
        for s in state.subjects
    ]
    df = pd.DataFrame(rows).set_index("id")
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Select": st.column_config.CheckboxColumn("Select"),
            "Name": st.column_config.TextColumn("Name"),
            "Deadline": st.column_config.DateColumn("Deadline"),
            "Difficulty": st.column_config.SelectboxColumn(
                "Difficulty", options=[1, 2, 3, 4, 5]
            ),
            "Est hours": st.column_config.NumberColumn(
                "Est hours", format="%.1f", min_value=0.5, max_value=200.0, step=0.5
            ),
            "Notes": st.column_config.TextColumn("Notes", width="medium"),
        },
        key=f"subjects_editor_{current_profile}",
    )

    edited_records = edited.reset_index().to_dict("records")
    selected_ids = [row["id"] for row in edited_records if row.get("Select")]
    selected_names = [row["Name"] for row in edited_records if row.get("Select")]

    col_apply, col_delete = st.columns([1, 1])

    if col_apply.button("Apply changes"):
        id_to_subject = {s.id: s for s in state.subjects}
        name_updates = {}
        updated_subjects = []
        for row in edited_records:
            subject = id_to_subject.get(row["id"])
            if not subject:
                continue
            new_name = str(row.get("Name") or "").strip()
            if not new_name:
                st.warning("Subject name cannot be empty.")
                return
            deadline = _coerce_date(row.get("Deadline")) or subject.deadline
            difficulty_value = row.get("Difficulty")
            est_value = row.get("Est hours")
            notes_value = row.get("Notes")
            difficulty = (
                subject.difficulty
                if difficulty_value is None or pd.isna(difficulty_value)
                else int(difficulty_value)
            )
            est_hours = (
                subject.est_hours
                if est_value is None or pd.isna(est_value)
                else float(est_value)
            )
            notes = "" if notes_value is None or pd.isna(notes_value) else str(notes_value)

            if new_name != subject.name:
                name_updates[subject.id] = new_name

            subject.name = new_name
            subject.deadline = deadline
            subject.difficulty = difficulty
            subject.est_hours = est_hours
            subject.notes = notes
            updated_subjects.append(subject)

        if name_updates:
            for task in state.tasks:
                if task.subject_id in name_updates:
                    task.subject_name = name_updates[task.subject_id]

        state.subjects = updated_subjects
        save_profile(current_profile, state)
        _queue_toast("Subjects updated.")
        st.rerun()

    if col_delete.button("Delete selected"):
        if not selected_ids:
            st.warning("Select at least one subject to delete.")
        else:

            @st.dialog("Delete selected subjects?")
            def _confirm_subject_delete() -> None:
                st.write("This will remove the subjects and their tasks.")
                st.write(", ".join(selected_names))
                if st.button("Delete", type="primary"):
                    state.subjects = [s for s in state.subjects if s.id not in selected_ids]
                    state.tasks = [t for t in state.tasks if t.subject_id not in selected_ids]
                    save_profile(current_profile, state)
                    _queue_toast("Subjects deleted.")
                    st.rerun()

            _confirm_subject_delete()


def render_calendar(state: AppState) -> None:
    st.header("Calendar")

    st.subheader("Import calendar (.ics)")
    uploaded = st.file_uploader("Upload .ics file", type=["ics"], key="ics_upload")
    parsed_events = []
    if uploaded:
        try:
            parsed_events = parse_ics_bytes(uploaded.read())
        except Exception as e:
            st.error(f"Could not read ICS file: {e}")
        else:
            if not parsed_events:
                st.warning("No events found in this file.")

    if parsed_events:
        preview = [
            {
                "Title": ev.title,
                "Start": ev.start,
                "End": ev.end,
                "Duration (m)": int((ev.end - ev.start).total_seconds() // 60),
            }
            for ev in parsed_events
        ]
        st.dataframe(preview, use_container_width=True, height=250)
        import_mode = st.radio(
            "Import mode", ["Merge", "Replace"], horizontal=True, index=0
        )
        if st.button("Import events", type="primary"):
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
            st.toast("Events imported.")

    st.divider()
    st.subheader("Saved events")
    if not state.events:
        st.info("No calendar events stored yet.")
        return

    sorted_events = sorted(state.events, key=lambda x: x.start)
    event_rows = [
        {
            "Select": False,
            "id": ev.id,
            "Title": ev.title,
            "Start": ev.start.strftime("%Y-%m-%d %H:%M"),
            "End": ev.end.strftime("%Y-%m-%d %H:%M"),
            "Duration (m)": int((ev.end - ev.start).total_seconds() // 60),
        }
        for ev in sorted_events
    ]

    with st.form("convert_events_form"):
        events_df = pd.DataFrame(event_rows).set_index("id")
        selected_table = st.data_editor(
            events_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Select": st.column_config.CheckboxColumn("Select"),
                "Title": st.column_config.TextColumn("Title"),
                "Start": st.column_config.TextColumn("Start"),
                "End": st.column_config.TextColumn("End"),
                "Duration (m)": st.column_config.NumberColumn("Duration (m)", format="%d"),
            },
            disabled=["Title", "Start", "End", "Duration (m)"],
            key=f"events_convert_editor_{current_profile}",
        )

        st.subheader("Convert selected to subjects")
        default_difficulty = st.selectbox(
            "Default difficulty", [1, 2, 3, 4, 5], index=2
        )
        default_hours = st.number_input(
            "Estimated hours (each)", min_value=0.5, max_value=200.0, value=2.0, step=0.5
        )
        remove_converted = st.checkbox(
            "Remove converted events from calendar", value=True
        )
        convert = st.form_submit_button("Convert selected to subjects")

    if convert:
        selected_records = selected_table.reset_index().to_dict("records")
        selected_ids = {row["id"] for row in selected_records if row.get("Select")}
        if not selected_ids:
            st.warning("Select at least one event to convert.")
        else:
            for ev in sorted_events:
                if ev.id not in selected_ids:
                    continue
                state.subjects.append(
                    Subject(
                        id=str(uuid4()),
                        name=ev.title,
                        deadline=ev.start.date(),
                        difficulty=int(default_difficulty),
                        est_hours=float(default_hours),
                        notes="Imported from calendar",
                    )
                )
            if remove_converted:
                state.events = [ev for ev in sorted_events if ev.id not in selected_ids]
            save_profile(current_profile, state)
            _queue_toast("Events converted to subjects.")
            st.rerun()

    if st.button("Clear all events"):

        @st.dialog("Clear all events?")
        def _confirm_clear_events() -> None:
            st.write("This will remove all imported events.")
            if st.button("Clear events", type="primary"):
                state.events = []
                save_profile(current_profile, state)
                _queue_toast("Events cleared.")
                st.rerun()

        _confirm_clear_events()


def render_plan(state: AppState) -> None:
    st.header("Plan")

    today = date.today()
    default_week_start = state.last_generated_on or today

    week_start = default_week_start
    col_left, col_right = st.columns([1, 2])

    with col_left:
        st.subheader("Today")
        today_tasks = [t for t in state.tasks if t.day == today]
        total_minutes_today = sum(t.minutes for t in today_tasks)
        st.metric("Total minutes today", total_minutes_today)

        if not today_tasks:
            st.info("No tasks scheduled for today.")
        else:
            today_sorted = sorted(today_tasks, key=lambda t: (t.done, t.subject_name.lower()))
            top_tasks = today_sorted[:3]
            updates = {}
            for task in top_tasks:
                label = f"{task.subject_name} - {task.minutes}m"
                checked = st.checkbox(label, value=task.done, key=f"today_done_{task.id}")
                updates[task.id] = checked

            if len(today_tasks) > len(top_tasks):
                st.caption("More tasks available in the weekly plan.")

            changed = any(task.done != updates.get(task.id, task.done) for task in top_tasks)
            if changed and st.button("Save today updates"):
                for task in today_tasks:
                    if task.id in updates:
                        task.done = bool(updates[task.id])
                save_profile(current_profile, state)
                st.toast("Today updated.")

        if st.button("Reschedule overdue"):
            state.tasks = reschedule_overdue(
                state.tasks, state.settings, today, state.events
            )
            save_profile(current_profile, state)
            st.toast("Overdue tasks moved forward.")

    with col_right:
        st.subheader("This Week")
        date_col, action_col = st.columns([1, 1])
        with date_col:
            week_start = st.date_input("Week start", value=default_week_start)
        with action_col:
            if st.button("Generate / Refresh plan", type="primary"):
                state.tasks = generate_week_plan(
                    state.subjects, state.settings, week_start, state.tasks, state.events
                )
                state.last_generated_on = week_start
                save_profile(current_profile, state)
                st.toast("Plan generated.")
        week_end = week_start + timedelta(days=6)
        st.caption(f"Week: {week_start.isoformat()} - {week_end.isoformat()}")

        subject_options = ["All subjects"] + sorted({s.name for s in state.subjects})
        filter_col, done_col = st.columns([2, 1])
        with filter_col:
            chosen_subject = st.selectbox("Subject filter", subject_options)
        with done_col:
            show_done = st.checkbox("Show done", value=True)

        week_tasks = [t for t in state.tasks if week_start <= t.day <= week_end]
        filtered_tasks = [
            t for t in week_tasks
            if (chosen_subject == "All subjects" or t.subject_name == chosen_subject)
            and (show_done or not t.done)
        ]

        if not filtered_tasks:
            st.info("No tasks to show for this week.")
        else:
            table_rows = [
                {
                    "id": t.id,
                    "Date": t.day,
                    "Day": t.day.strftime("%a"),
                    "Subject": t.subject_name,
                    "Minutes": t.minutes,
                    "Done": t.done,
                    "Notes": t.notes,
                }
                for t in sorted(filtered_tasks, key=lambda x: (x.day, x.subject_name.lower()))
            ]
            df = pd.DataFrame(table_rows).set_index("id")
            edited = st.data_editor(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Date": st.column_config.DateColumn("Date"),
                    "Day": st.column_config.TextColumn("Day"),
                    "Subject": st.column_config.TextColumn("Subject"),
                    "Minutes": st.column_config.NumberColumn("Minutes", format="%d"),
                    "Done": st.column_config.CheckboxColumn("Done"),
                    "Notes": st.column_config.TextColumn("Notes", width="medium"),
                },
                disabled=["Date", "Day", "Subject", "Minutes"],
                key=f"week_table_editor_{current_profile}_{week_start.isoformat()}",
            )

            edited_records = edited.reset_index().to_dict("records")
            updates = {row["id"]: row for row in edited_records}
            pending = []
            for task in filtered_tasks:
                row = updates.get(task.id)
                if not row:
                    continue
                new_done = bool(row.get("Done"))
                new_notes = row.get("Notes") or ""
                if task.done != new_done or (task.notes or "") != new_notes:
                    pending.append((task, new_done, new_notes))

            if pending and st.button("Save changes"):
                for task, new_done, new_notes in pending:
                    task.done = new_done
                    task.notes = new_notes
                save_profile(current_profile, state)
                st.toast("Changes saved.")

            week_total = sum(t.minutes for t in filtered_tasks)
            week_done = sum(t.minutes for t in filtered_tasks if t.done)
            week_remaining = week_total - week_done
            m1, m2, m3 = st.columns(3)
            m1.metric("Week planned (m)", week_total)
            m2.metric("Done (m)", week_done)
            m3.metric("Remaining (m)", week_remaining)

            busy_by_day = compute_busy_minutes_by_day(state.events, week_start, num_days=7)
            day_totals = []
            for i in range(7):
                d = week_start + timedelta(days=i)
                day_tasks = [t for t in filtered_tasks if t.day == d]
                planned = sum(t.minutes for t in day_tasks)
                done = sum(t.minutes for t in day_tasks if t.done)
                busy = busy_by_day.get(d, 0)
                capacity = max(
                    0,
                    state.settings.minutes_per_day
                    - busy
                    - state.settings.daily_buffer_minutes,
                )
                day_totals.append(
                    {
                        "Date": d.strftime("%a %m/%d"),
                        "Planned (m)": planned,
                        "Done (m)": done,
                        "Remaining (m)": planned - done,
                        "Busy (m)": busy,
                        "Capacity after busy (m)": capacity,
                    }
                )

            with st.expander("Per-day totals", expanded=False):
                st.table(day_totals)

    st.divider()
    risk_items = build_risk_list(state.subjects, state.tasks, today)
    with st.expander("Risk list", expanded=False):
        if not risk_items:
            st.info("No risky subjects right now.")
        else:
            badge = {"HIGH": "🔴 HIGH", "MED": "🟠 MED", "LOW": "🟢 LOW"}
            risk_rows = []
            for r in risk_items:
                risk_rows.append(
                    {
                        "Subject": r["subject"],
                        "Level": badge.get(r["level"], r["level"]),
                        "Days left": r["days_left"],
                        "Remaining (m)": r["remaining_minutes"],
                        "Remaining (h)": r["remaining_hours"],
                        "Suggested m today": r["suggested_today_minutes"],
                        "Difficulty": r["difficulty"],
                        "Deadline": r["deadline"],
                    }
                )
            st.dataframe(risk_rows, use_container_width=True, height=240)

    st.divider()
    st.subheader("Exports")
    week_tasks = [t for t in state.tasks if week_start <= t.day <= week_end]
    if week_tasks:
        ics_bytes, ics_warnings = tasks_to_ics(
            week_tasks, week_start, state.settings, state.events
        )
        st.download_button(
            "Download ICS",
            data=ics_bytes,
            file_name=f"study_plan_{week_start.isoformat()}.ics",
            mime="text/calendar",
        )
        if ics_warnings:
            st.warning(" | ".join(ics_warnings))

        pdf_bytes = week_plan_to_pdf(
            week_tasks, state.settings, week_start, risk_items
        )
        st.download_button(
            "Download PDF",
            data=pdf_bytes,
            file_name=f"study_plan_{week_start.isoformat()}.pdf",
            mime="application/pdf",
        )
    else:
        st.info("No tasks to export for this week.")


def render_progress(state: AppState) -> None:
    st.header("Progress")

    total_minutes = sum(t.minutes for t in state.tasks)
    done_minutes = sum(t.minutes for t in state.tasks if t.done)
    remaining_minutes = total_minutes - done_minutes

    a, b, c = st.columns(3)
    a.metric("Total planned minutes", total_minutes)
    b.metric("Completed minutes", done_minutes)
    c.metric("Remaining minutes", remaining_minutes)

    st.divider()
    st.subheader("By subject")
    if not state.subjects:
        st.info("No subjects yet.")
        return

    subject_rows = []
    for subj in state.subjects:
        subj_tasks = [t for t in state.tasks if t.subject_id == subj.id]
        total = sum(t.minutes for t in subj_tasks)
        done = sum(t.minutes for t in subj_tasks if t.done)
        remaining = total - done
        completion_rate = (done / total) if total else 0
        subject_rows.append(
            {
                "Subject": subj.name,
                "Completion %": round(completion_rate * 100, 1),
                "Done (m)": done,
                "Total (m)": total,
                "Remaining (m)": remaining,
                "Deadline": subj.deadline.isoformat(),
            }
        )

    df = pd.DataFrame(subject_rows).sort_values(by="Completion %", ascending=True)
    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            "Completion %": st.column_config.NumberColumn(
                "Completion %", format="%.1f%%"
            )
        },
    )


def render_settings(state: AppState) -> None:
    st.header("Settings")

    st.subheader("Essentials")
    state.settings.minutes_per_day = st.slider(
        "Minutes per day", 15, 600, state.settings.minutes_per_day, 15
    )
    state.settings.rest_days = st.multiselect(
        "Rest days (0=Mon)",
        options=list(range(7)),
        format_func=lambda x: DAY_LABELS[x],
        default=state.settings.rest_days,
    )

    with st.expander("Advanced settings", expanded=False):
        chunk_options = [25, 45, 60]
        chunk_idx = (
            chunk_options.index(state.settings.chunk_minutes)
            if state.settings.chunk_minutes in chunk_options
            else 0
        )
        state.settings.chunk_minutes = st.selectbox(
            "Chunk size (minutes)", chunk_options, index=chunk_idx
        )
        state.settings.daily_buffer_minutes = st.slider(
            "Daily buffer (minutes)", 0, 120, state.settings.daily_buffer_minutes, 5
        )
        start_hour, end_hour = st.slider(
            "Preferred study hours",
            0,
            23,
            (state.settings.preferred_start_hour, state.settings.preferred_end_hour),
        )
        state.settings.preferred_start_hour = start_hour
        state.settings.preferred_end_hour = end_hour

    if st.button("Save settings", type="primary"):
        save_profile(current_profile, state)
        st.toast("Settings saved.")

    if st.button("Reset current profile (keep settings)"):

        @st.dialog("Reset current profile?")
        def _confirm_reset() -> None:
            st.write("This will clear subjects, tasks, and events. Settings stay.")
            if st.button("Reset profile", type="primary"):
                state.subjects = []
                state.tasks = []
                state.events = []
                save_profile(current_profile, state)
                _queue_toast("Profile reset.")
                st.rerun()

        _confirm_reset()


profiles = _ensure_session_state()
state: AppState = st.session_state.state
current_profile = st.session_state.profile_name

st.title("Study Planner")
st.caption("Local-first planner with calendar import/export, profiles, and weekly views.")
_flush_toast()

if "nav_page" not in st.session_state:
    st.session_state.nav_page = "Setup"
if not state.subjects:
    st.session_state.nav_page = "Setup"

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

    with st.form("create_profile_form"):
        new_profile_name = st.text_input("New profile name", placeholder="e.g. Semester A")
        if st.form_submit_button("Create profile"):
            try:
                new_state = create_profile(new_profile_name)
            except ValueError as e:
                st.error(str(e))
            else:
                _queue_toast(f"Profile '{new_profile_name.strip()}' created.")
                st.session_state.profile_name = new_profile_name.strip()
                st.session_state.state = new_state
                st.rerun()

    if st.button("Delete profile", disabled=len(profiles) <= 1):
        if len(profiles) <= 1:
            st.warning("Cannot delete the last profile.")
        else:

            @st.dialog("Delete profile?")
            def _confirm_delete_profile() -> None:
                st.write(f"Delete profile '{current_profile}' and its data?")
                if st.button("Delete", type="primary"):
                    delete_profile(current_profile)
                    remaining = list_profiles()
                    _switch_profile(remaining[0])
                    _queue_toast("Profile deleted.")
                    st.rerun()

            _confirm_delete_profile()

    st.divider()
    st.header("Navigate")
    pages = ["Setup", "Calendar", "Plan", "Progress", "Settings"]
    page = st.radio("", pages, key="nav_page")

    st.caption("Workflow: Setup -> Calendar -> Plan -> Progress")
    st.caption("Data is stored locally in the .data folder inside this project.")

if page == "Setup":
    render_setup(state)
elif page == "Calendar":
    render_calendar(state)
elif page == "Plan":
    render_plan(state)
elif page == "Progress":
    render_progress(state)
elif page == "Settings":
    render_settings(state)
