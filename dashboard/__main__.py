"""Dashboard package entrypoint.

Run with:
    python -m dashboard
"""

from .app import app

if __name__ == "__main__":
    app.run(debug=True, port=8050)
