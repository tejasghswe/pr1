import sys
from pathlib import Path

# Ensure `handoff_copilot/` (the repo root for this project's imports, e.g.
# `schemas`, `app.*`, `redflag_service.*`) is importable regardless of how
# pytest was invoked.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
