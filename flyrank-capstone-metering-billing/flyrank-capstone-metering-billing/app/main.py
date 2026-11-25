"""Start the service:  python -m app.main     (listens on PORT, default 8080)"""
from __future__ import annotations

import os

import uvicorn

from app.api import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
