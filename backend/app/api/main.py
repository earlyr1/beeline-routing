"""Точка входа uvicorn: uvicorn app.api.main:app"""

from app.api.app import create_app

app = create_app()
