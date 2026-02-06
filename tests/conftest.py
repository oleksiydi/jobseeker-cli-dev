from __future__ import annotations

import sys
from pathlib import Path


# Ensure imports like `import app.analyze_jobs` work regardless of pytest launch mode.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

