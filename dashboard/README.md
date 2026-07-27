# HealthTrack Dashboard
**Author:** Arun Bhaskar Gadde | MSCS-699-B01 Capstone 

This dashboard provides a real-time monitoring UI for HealthTrack.

## Run locally

```powershell
cd C:\Users\arung\VSCode_Projects\MSCS-699-B01-Cpastone
.\.venv\Scripts\Activate.ps1
python -m dashboard.app
```

## Features

- Real-time dashboard layout with vital signs and activity trends
- Alert panel with color-coded abnormal readings
- Automatic refresh every 5 seconds

## Notes

This dashboard is currently a standalone Dash app. It can be integrated with the existing FastAPI backend via a WebSocket or REST endpoint for live data.
