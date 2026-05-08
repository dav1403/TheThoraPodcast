"""Save episode counts for each feed before the run starts."""
import json
from pathlib import Path

counts = {}
for f in Path("feeds").glob("*.entries.json"):
    slug = f.stem.replace(".entries", "")
    try:
        entries = json.loads(f.read_text(encoding="utf-8") or "[]")
    except Exception:
        entries = []
    counts[slug] = len(entries)
Path("/tmp/pre_run_counts.json").write_text(json.dumps(counts))
print("Pre-run counts:", counts)
