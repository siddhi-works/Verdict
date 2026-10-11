"""Vercel entry point: exposes the web API as an ASGI `app`. All routes live under /api."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.demo import create_demo_app  # noqa: E402

app = create_demo_app()
