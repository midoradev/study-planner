# Study Planner

Local-first study planning app for building weekly schedules without uploading data to any server.

## Features

- Multi-profile workflow
- Subject and task planning with weekly schedules
- Calendar import/export via .ics
- PDF export for weekly plans
- Risk list and progress tracking

## Data storage

All data is stored locally in `.data/` and is ignored by git.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Basic workflow

1. Create a profile
2. Add subjects
3. (Optional) Import calendar `.ics`
4. Generate the plan
5. Export `.ics` or PDF
6. Mark tasks done

## License

See `LICENSE`.
