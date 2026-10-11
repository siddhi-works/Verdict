"""
scripts/dev_web.py

Runs the Vercel deployment locally: the static front end in public/ and the web API on one origin.

    python -m scripts.dev_web            # then open http://localhost:3000
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from service.demo import create_demo_app  # noqa: E402

app = Starlette(routes=[
    Mount("/api", app=create_demo_app()),  # create_demo_app routes already start with /api, mounted below for clarity
])

# create_demo_app() defines absolute /api/... routes, so serve it at the root and fall back to static files.
api = create_demo_app()
api.router.routes.append(Mount("/", app=StaticFiles(directory=str(ROOT / "public"), html=True), name="static"))
app = api

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    uvicorn.run(app, host="127.0.0.1", port=port)
