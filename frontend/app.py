"""
PHASES 6-8 - Streamlit frontend: chat UI, tables, charts, explanations, follow-up questions.

Run:  streamlit run frontend/app.py        (backend must be running on API_URL)
"""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

EXAMPLES = [
    "Show me the top 5 departments with the highest average salary",
    "Show sales by month",
    "Show sales by category as a pie chart",
    "Which are our top 5 products by revenue?",
    "Why did sales decrease in March 2025?",
    "How many employees are in each department?",
]

st.set_page_config(page_title="AI SQL Assistant", page_icon="🗄️", layout="wide")


# --------------------------------------------------------------------------- API helpers
def api(method: str, path: str, **kwargs):
    try:
        resp = requests.request(method, f"{API_URL}{path}", headers=HEADERS, timeout=120, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        detail = exc.response.json().get("detail", exc.response.text) if exc.response is not None else str(exc)
        return {"_error": f"API error: {detail}"}
    except requests.RequestException as exc:
        return {"_error": f"Cannot reach the backend at {API_URL}: {exc}"}


@st.cache_data(ttl=300, show_spinner=False)
def load_schema():
    return api("GET", "/api/schema")


# --------------------------------------------------------------------------- rendering
def render_chart(spec: dict, df: pd.DataFrame) -> None:
    x, ys, series, kind = spec["x"], spec["y"], spec.get("series"), spec["type"]
    if x not in df.columns or any(y not in df.columns for y in ys):
        return
    st.caption(spec.get("title") or "")
    if kind == "pie":
        chart = alt.Chart(df).mark_arc(innerRadius=50).encode(
            theta=alt.Theta(f"{ys[0]}:Q"), color=alt.Color(f"{x}:N"), tooltip=[x, ys[0]])
        st.altair_chart(chart, use_container_width=True)
        return
    data = df.pivot_table(index=x, columns=series, values=ys[0], aggfunc="sum") if series else df.set_index(x)[ys]
    (st.line_chart if kind == "line" else st.bar_chart)(data)


def render_result(msg: dict, key: str) -> None:
    if msg.get("error"):
        st.error(msg["error"])
    if msg.get("answer"):
        st.markdown(msg["answer"])

    for step_no, step in enumerate(msg.get("steps") or [], start=1):
        with st.expander(f"Step {step_no}: {step['thought']}"):
            if step.get("sql"):
                st.code(step["sql"], language="sql")
            if step.get("error"):
                st.warning(step["error"])
            elif step.get("rows"):
                st.dataframe(pd.DataFrame(step["rows"], columns=step["columns"]), use_container_width=True)

    if msg.get("columns") and msg.get("rows"):
        df = pd.DataFrame(msg["rows"], columns=msg["columns"])
        if msg.get("chart"):
            render_chart(msg["chart"], df)
        tab_table, tab_sql = st.tabs(["Table", "SQL & details"])
        with tab_table:
            st.dataframe(df, use_container_width=True)
            st.download_button("Download CSV", df.to_csv(index=False), "result.csv", "text/csv", key=f"dl-{key}")
        with tab_sql:
            st.code(msg.get("sql") or "", language="sql")
            if msg.get("query_explanation"):
                st.markdown(f"**What this query does:** {msg['query_explanation']}")
            for a in msg.get("assumptions") or []:
                st.caption(f"Assumption: {a}")
            for hint in msg.get("optimizations") or []:
                st.caption(f"Optimization tip: {hint}")
            for w in msg.get("warnings") or []:
                st.caption(f"Note: {w}")
            st.caption(f"{msg.get('row_count', 0)} rows · {msg.get('elapsed_ms', 0)} ms · intent: {msg.get('intent')}")
    elif msg.get("sql") and msg.get("error"):
        st.code(msg["sql"], language="sql")

    if msg.get("follow_ups"):
        st.caption("Ask next:")
        cols = st.columns(len(msg["follow_ups"]))
        for i, (col, q) in enumerate(zip(cols, msg["follow_ups"])):
            if col.button(q, key=f"fu-{key}-{i}", use_container_width=True):
                st.session_state.pending = q
                st.rerun()


# --------------------------------------------------------------------------- state
if "messages" not in st.session_state:
    st.session_state.messages = []       # [{"role": "user"|"assistant", ...}]
    st.session_state.session_id = None
    st.session_state.pending = None

# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🗄️ AI SQL Assistant")
    st.caption("Ask questions about your data in plain English.")
    if st.button("New conversation", use_container_width=True):
        if st.session_state.session_id:
            api("DELETE", f"/api/sessions/{st.session_state.session_id}")
        st.session_state.messages, st.session_state.session_id = [], None
        st.rerun()

    st.subheader("Try asking")
    for i, ex in enumerate(EXAMPLES):
        if st.button(ex, key=f"ex-{i}", use_container_width=True):
            st.session_state.pending = ex
            st.rerun()

    st.subheader("Database schema")
    schema = load_schema()
    if "_error" in schema:
        st.warning(schema["_error"])
    else:
        for t in schema["tables"]:
            with st.expander(f"{t['name']} ({t['row_count']})"):
                for c in t["columns"]:
                    ref = f" → {c['references']}" if c.get("references") else ""
                    st.caption(f"{c['name']} · {c['type']}{ref}")

# --------------------------------------------------------------------------- main chat
st.header("Ask your database")
if not st.session_state.messages:
    st.info("Try: *Show me the top 5 departments with the highest average salary* - then follow up with "
            "*now only for Engineering* style questions. Pick an example from the sidebar to start.")

for idx, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.markdown(m["content"])
        else:
            render_result(m["data"], key=str(idx))

question = st.chat_input("Ask a question about the data…") or st.session_state.pending
st.session_state.pending = None

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            data = api("POST", "/api/chat", json={"question": question, "session_id": st.session_state.session_id})
        if "_error" in data:
            data = {"error": data["_error"]}
        else:
            st.session_state.session_id = data.get("session_id")
        render_result(data, key=str(len(st.session_state.messages)))
    st.session_state.messages.append({"role": "assistant", "data": data})
