"""
Compatibility entrypoint.

Keeps `uvicorn main:app --reload` working while the application code lives in
`app/main.py`.
"""

from app.main import app
