import os
from pathlib import Path

HERE = Path(__file__).parent
# Mapping liegt im Repo unter config/mapping; im Image unter /app/mapping
os.environ.setdefault("EMS_MAPPING_DIR", str(next(p for p in [Path("/app/mapping"), HERE.parents[1] / "config" / "mapping"] if p.exists())))
