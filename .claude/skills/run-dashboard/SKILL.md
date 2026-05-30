---
name: run-dashboard
description: Launch the Streamlit correlation dashboard. Use when the user wants to view or test the visualization.
disable-model-invocation: false
---

Launch the Streamlit dashboard:

```bash
streamlit run app/streamlit_app.py
```

Before launching, check:
- `app/streamlit_app.py` exists — if not, it hasn't been built yet; tell the user it needs to be implemented first
- The ETL has been run and data exists in PostgreSQL (run `/run-etl` if not)
- The Python virtual environment is active

The dashboard opens at http://localhost:8501. If it doesn't open automatically, direct the user to navigate there manually. Monitor the terminal for errors and report any that appear on startup.
