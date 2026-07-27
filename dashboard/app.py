import json
import os
import sys
import urllib.error
import urllib.request
from dash import Dash, dcc, html, Input, Output
import plotly.graph_objs as go

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from dashboard.layout import create_dashboard_layout, create_empty_chart

BACKEND_BASE_URL = os.environ.get("DASHBOARD_BACKEND_URL", "http://127.0.0.1:8000")


def fetch_json(path: str):
    url = BACKEND_BASE_URL.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except urllib.error.URLError as exc:
        return {"error": str(exc)}


app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "HealthTrack Dashboard"
app.layout = create_dashboard_layout()


@app.callback(
    Output("patient-select", "options"),
    Output("patient-select", "value"),
    Output("vital-signs-chart", "figure"),
    Output("activity-chart", "figure"),
    Output("alert-panel", "children"),
    Input("refresh-interval", "n_intervals"),
    Input("patient-select", "value"),
)
def update_dashboard(n_intervals, selected_patient_id):
    patients_response = fetch_json("/dashboard/patients")
    if isinstance(patients_response, dict) and patients_response.get("error"):
        error_message = patients_response["error"]
        empty_figure = {
            "data": [],
            "layout": go.Layout(title="Unable to load data", margin={"l": 40, "r": 20, "t": 50, "b": 40}),
        }
        return [], None, empty_figure, empty_figure, [html.Div(f"Backend error: {error_message}")]

    patient_options = [
        {"label": f"{patient['name']} ({patient['mrn']})", "value": patient["id"]}
        for patient in patients_response
    ]
    if not selected_patient_id and patient_options:
        selected_patient_id = patient_options[0]["value"]

    if selected_patient_id is None:
        return patient_options, None, create_empty_chart("Vital Signs"), create_empty_chart("Activity"), [html.Div("No patient selected.")]

    live_data = fetch_json(f"/dashboard/patient/{selected_patient_id}/live")
    if isinstance(live_data, dict) and live_data.get("error"):
        error_message = live_data["error"]
        empty_figure = {
            "data": [],
            "layout": go.Layout(title="Unable to load data", margin={"l": 40, "r": 20, "t": 50, "b": 40}),
        }
        return patient_options, selected_patient_id, empty_figure, empty_figure, [html.Div(f"Backend error: {error_message}")]

    vital_data = live_data.get("vitals", [])
    activity_data = live_data.get("activities", [])
    alerts = live_data.get("alerts", [])

    vital_figure = {
        "data": [
            go.Scatter(
                x=[item["recorded_at"] for item in vital_data if item["vital_type"] == "heart_rate"],
                y=[item["value"] for item in vital_data if item["vital_type"] == "heart_rate"],
                mode="lines+markers",
                name="Heart Rate",
                line={"color": "#e74c3c"},
            ),
            go.Scatter(
                x=[item["recorded_at"] for item in vital_data if item["vital_type"] == "spo2"],
                y=[item["value"] for item in vital_data if item["vital_type"] == "spo2"],
                mode="lines+markers",
                name="SpO2",
                line={"color": "#3498db"},
                yaxis="y2",
            ),
        ],
        "layout": go.Layout(
            title="Vital Signs Trend",
            xaxis={"title": "Recorded At"},
            yaxis={"title": "Heart Rate (bpm)", "range": [50, 140]},
            yaxis2={"title": "SpO2 (%)", "overlaying": "y", "side": "right", "range": [80, 100]},
            legend={"x": 0, "y": 1.1, "orientation": "h"},
            margin={"l": 40, "r": 60, "t": 50, "b": 40},
            height=400,
        ),
    }

    activity_figure = {
        "data": [
            go.Bar(
                x=[item["recorded_date"] for item in activity_data if item["activity_type"] == "steps"],
                y=[item["value"] for item in activity_data if item["activity_type"] == "steps"],
                name="Steps",
                marker={"color": "#2ecc71"},
            ),
            go.Scatter(
                x=[item["recorded_date"] for item in activity_data if item["activity_type"] == "active_minutes"],
                y=[item["value"] for item in activity_data if item["activity_type"] == "active_minutes"],
                mode="lines+markers",
                name="Active Minutes",
                line={"color": "#f1c40f"},
                yaxis="y2",
            ),
        ],
        "layout": go.Layout(
            title="Activity Trend",
            xaxis={"title": "Recorded Date"},
            yaxis={"title": "Steps"},
            yaxis2={"title": "Active Minutes", "overlaying": "y", "side": "right"},
            legend={"x": 0, "y": 1.1, "orientation": "h"},
            margin={"l": 40, "r": 60, "t": 50, "b": 40},
            height=400,
        ),
    }

    alert_cards = []
    if not alerts:
        alert_cards = [html.Div("No active alerts for this patient.")]
    else:
        for alert in alerts:
            color = "#e74c3c" if alert["severity"] == "critical" else "#f39c12"
            alert_cards.append(
                html.Div(
                    children=[
                        html.H3(alert["rule_type"], style={"margin": "0 0 6px 0"}),
                        html.P(alert["message"], style={"margin": "0 0 6px 0"}),
                        html.Small(f"Severity: {alert['severity']} | Status: {alert['status']}"),
                    ],
                    style={
                        "border": f"2px solid {color}",
                        "borderRadius": "8px",
                        "padding": "12px",
                        "marginBottom": "10px",
                        "backgroundColor": "#fff5f5" if alert["severity"] == "critical" else "#fff8e1",
                    },
                )
            )

    return patient_options, selected_patient_id, vital_figure, activity_figure, alert_cards


if __name__ == "__main__":
    app.run_server(debug=True, port=8050)
