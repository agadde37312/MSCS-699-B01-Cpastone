"""
HealthTrack real-time monitoring dashboard.

Polls the FastAPI backend for the latest alerts and risk assessments and
renders them as a live-updating dashboard. Run alongside the API:

    uvicorn app.main:app --reload          # terminal 1
    python dashboard/dash_app.py           # terminal 2

Then open http://127.0.0.1:8050
"""
import os
import requests
import pandas as pd
from dash import Dash, html, dcc, Output, Input, dash_table
import plotly.express as px

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
REFRESH_MS = 5000

app = Dash(__name__, title="HealthTrack Dashboard")

RISK_COLORS = {
    "Low": "#2ecc71", "Moderate": "#f1c40f",
    "High": "#e67e22", "Critical": "#e74c3c",
}

app.layout = html.Div(style={"fontFamily": "Arial, sans-serif", "padding": "20px"}, children=[
    html.H2("HealthTrack — Real-Time Patient Monitoring"),
    html.Div(id="connection-status", style={"marginBottom": "15px", "color": "#666"}),

    dcc.Interval(id="refresh-interval", interval=REFRESH_MS, n_intervals=0),

    html.Div(style={"display": "flex", "gap": "20px"}, children=[
        html.Div(style={"flex": 1}, children=[
            html.H4("Open Alerts"),
            dash_table.DataTable(
                id="alerts-table",
                columns=[
                    {"name": "Patient ID", "id": "patient_id"},
                    {"name": "Severity", "id": "severity"},
                    {"name": "Status", "id": "status"},
                    {"name": "Message", "id": "message"},
                    {"name": "Created", "id": "created_at"},
                ],
                style_cell={"textAlign": "left", "padding": "6px", "fontSize": "13px"},
                style_data_conditional=[
                    {"if": {"filter_query": '{severity} = "Critical"'}, "backgroundColor": "#fdecea"},
                    {"if": {"filter_query": '{severity} = "Warning"'}, "backgroundColor": "#fff8e1"},
                ],
                page_size=8,
            ),
        ]),
        html.Div(style={"flex": 1}, children=[
            html.H4("Alert Severity Breakdown"),
            dcc.Graph(id="severity-chart"),
        ]),
    ]),
])


@app.callback(
    Output("alerts-table", "data"),
    Output("severity-chart", "figure"),
    Output("connection-status", "children"),
    Input("refresh-interval", "n_intervals"),
)
def refresh(_n):
    try:
        resp = requests.get(f"{API_BASE}/alerts", params={"status": "Open"}, timeout=3)
        resp.raise_for_status()
        alerts = resp.json()
        status_msg = f"Connected to {API_BASE} — last refresh OK"
    except Exception as e:
        alerts = []
        status_msg = f"Could not reach API at {API_BASE} ({e})"

    df = pd.DataFrame(alerts) if alerts else pd.DataFrame(
        columns=["patient_id", "severity", "status", "message", "created_at"]
    )

    if not df.empty:
        counts = df["severity"].value_counts().reset_index()
        counts.columns = ["severity", "count"]
    else:
        counts = pd.DataFrame({"severity": [], "count": []})

    fig = px.bar(
        counts, x="severity", y="count", color="severity",
        color_discrete_map=RISK_COLORS, title="Open Alerts by Severity"
    )
    fig.update_layout(showlegend=False, margin=dict(t=40, b=20))

    return df.to_dict("records"), fig, status_msg


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
