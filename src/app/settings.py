"""Configuration access wrapper - delegates to src.shared.config"""
from src.shared.config import get_cors_origins, get_system_api_token, settings

__all__ = ["get_cors_origins", "get_system_api_token", "settings"]
