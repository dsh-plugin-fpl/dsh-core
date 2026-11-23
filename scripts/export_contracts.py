"""Export the independent service contract without reading live configuration or state."""

import json
import tempfile
from pathlib import Path

from dsh_core_worker.service import create_app

root = Path(__file__).resolve().parents[1]
(root / ".local").mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=root / ".local", prefix="contracts-") as directory:
    app = create_app(Path(directory))
    output = root / "web/src/generated/openapi.json"
    output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
print("Exported Core OpenAPI")
