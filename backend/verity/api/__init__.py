"""HTTP layer: FastAPI app, auth, rate limits, SSE (builder E)."""

from .app import create_app

__all__ = ["create_app"]
