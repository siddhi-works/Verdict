"""
Vercel entry point: exposes the web API as an ASGI `app`.

vercel.json rewrites /api/<anything> to this function as /api/index?__p=<anything>, because the function
file is api/index.py. RestorePath puts the original path back before routing, so the app's routes
(/api/health, /api/audits, ...) are the same locally and on Vercel.
"""
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.demo import create_demo_app  # noqa: E402


class RestorePath:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            pairs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
            original = next((v for k, v in pairs if k == "__p"), None)
            if original is not None:
                scope = dict(scope)
                path = "/api/" + original.strip("/")
                scope["path"] = path.rstrip("/") or "/api"
                scope["raw_path"] = scope["path"].encode()
                scope["query_string"] = urlencode([(k, v) for k, v in pairs if k != "__p"]).encode()
        await self.inner(scope, receive, send)


app = RestorePath(create_demo_app())
