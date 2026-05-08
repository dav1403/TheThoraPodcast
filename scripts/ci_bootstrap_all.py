"""Bootstrap channels that have no feed entries yet (backfill last 10 episodes)."""
import json
import subprocess
import sys
from pathlib import Path

channels = json.loads(Path("channels.json").read_text(encoding="utf-8-sig"))
for ch in channels:
    if not ch.get("enabled", True) or ch.get("source") == "rss":
        continue
    entries_file = Path(f"feeds/{ch['slug']}.entries.json")
    needs_bootstrap = (
        not entries_file.exists()
        or json.loads(entries_file.read_text(encoding="utf-8-sig") or "[]") == []
    )
    if needs_bootstrap:
        print(f"Bootstrapping {ch['slug']}...")
        result = subprocess.run(
            [sys.executable, "scripts/bootstrap_channel.py", "--slug", ch["slug"], "--max", "10"],
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"Bootstrap failed for {ch['slug']}")
