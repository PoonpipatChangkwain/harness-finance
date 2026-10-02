"""Write the FastAPI OpenAPI document for the frontend's typed client (``pnpm gen:api``).

uv run python -m app.api.export_openapi
"""

from __future__ import annotations

import json
from pathlib import Path

from app.api.main import app

OUT = Path(__file__).resolve().parents[2] / "schemas" / "openapi.json"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
