from dash import html, dcc
import plotly.graph_objs as go


def create_dashboard_layout():
    return html.Div(
        children=[
            html.Div(
                children=[
                    html.H1("HealthTrack Real-Time Monitoring Dashboard", className="dashboard-title"),
                    html.P(
                        "Monitor patient vital signs and activity data with live updates and alert indicators.",
                        className="dashboard-subtitle",
                    ),
                ],
                className="header",
            ),
            html.Div(
                children=[
                    html.Div(
                        children=[
                            html.Label("Select Patient"),
                            dcc.Dropdown(id="patient-select", placeholder="Pick a patient", clearable=False),
                        ],
                        className="selector-card",
                    ),
                ],
                className="selector-row",
            ),
            html.Div(
                children=[
                    html.Div(
                        children=[
                            html.H2("Latest Vital Signs"),
                            dcc.Graph(id="vital-signs-chart", config={"displayModeBar": False}),
                        ],
                        className="chart-card",
                    ),
                    html.Div(
                        children=[
                            html.H2("Activity Summary"),
                            dcc.Graph(id="activity-chart", config={"displayModeBar": False}),
                        ],
                        className="chart-card",
                    ),
                ],
                className="chart-row",
            ),
            html.Div(
                children=[
                    html.H2("Alerts"),
                    html.Div(id="alert-panel", className="alert-panel"),
                ],
                className="alert-card",
            ),
            dcc.Interval(id="refresh-interval", interval=5000, n_intervals=0),
        ],
        className="dashboard-container",
    )


def create_empty_chart(title: str):
    return {
        "data": [go.Scatter(x=[], y=[], mode="lines+markers", name=title)],
        "layout": go.Layout(
            title=title,
            xaxis={"title": "Time"},
            yaxis={"title": "Value"},
            margin={"l": 40, "r": 20, "t": 50, "b": 40},
        ),
    }
