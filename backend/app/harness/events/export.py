"""Write the event envelope JSON schema for the frontend.

uv run python -m app.harness.events.export
"""

from __future__ import annotations

import json
from pathlib import Path

from app.harness.events.schema import envelope_json_schema

OUT = Path(__file__).resolve().parents[3] / "schemas" / "events.schema.json"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(envelope_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
