"""Compatibility shim - delegates to src.app.main"""
from src.app.main import app, create_app

__all__ = ["app", "create_app"]
