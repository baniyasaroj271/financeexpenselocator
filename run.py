from __future__ import annotations

import os

from app import app


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HISABSMART_HOST", "127.0.0.1"),
        port=int(os.environ.get("HISABSMART_PORT", "5000")),
        debug=os.environ.get("HISABSMART_DEBUG", "false").lower() in {"1", "true", "yes"},
    )
