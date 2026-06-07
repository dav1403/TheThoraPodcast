import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("YOUTUBE_API_KEY", "test-key")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("GITHUB_REPO", "dav1403/TheThoraPodcast")

# Lightweight stubs so the test can import process_podcasts without full runtime deps.
feedgen_mod = types.ModuleType("feedgen")
feedgen_feed_mod = types.ModuleType("feedgen.feed")
feedgen_feed_mod.FeedGenerator = object
sys.modules.setdefault("feedgen", feedgen_mod)
sys.modules.setdefault("feedgen.feed", feedgen_feed_mod)

boto3_mod = types.ModuleType("boto3")
boto3_mod.client = lambda *args, **kwargs: None
sys.modules.setdefault("boto3", boto3_mod)

botocore_mod = types.ModuleType("botocore")
botocore_config_mod = types.ModuleType("botocore.config")
botocore_exceptions_mod = types.ModuleType("botocore.exceptions")
botocore_config_mod.Config = object
botocore_exceptions_mod.ClientError = Exception
sys.modules.setdefault("botocore", botocore_mod)
sys.modules.setdefault("botocore.config", botocore_config_mod)
sys.modules.setdefault("botocore.exceptions", botocore_exceptions_mod)

anthropic_mod = types.ModuleType("anthropic")
sys.modules.setdefault("anthropic", anthropic_mod)

import process_podcasts  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class YoutubeDiscoveryTests(unittest.TestCase):
    @patch("process_podcasts.discover_channel_tab_ids")
    @patch("process_podcasts.requests.get")
    def test_get_new_videos_includes_shorts_tab_ids(self, mock_get, mock_discover_tabs):
        mock_discover_tabs.return_value = ["short-1"]

        def fake_get(url, *args, **kwargs):
            if "playlistItems" in url:
                return _FakeResponse({
                    "items": [
                        {"snippet": {"resourceId": {"videoId": "regular-1"}}},
                    ]
                })
            return _FakeResponse({
                "items": [
                    {
                        "id": "regular-1",
                        "snippet": {
                            "title": "Regular video",
                            "description": "",
                            "publishedAt": "2026-06-07T12:00:00Z",
                            "thumbnails": {},
                            "liveBroadcastContent": "none",
                        },
                        "contentDetails": {"duration": "PT5M"},
                    },
                    {
                        "id": "short-1",
                        "snippet": {
                            "title": "Short video",
                            "description": "",
                            "publishedAt": "2026-06-07T13:00:00Z",
                            "thumbnails": {},
                            "liveBroadcastContent": "none",
                        },
                        "contentDetails": {"duration": "PT45S"},
                    },
                ]
            })

        mock_get.side_effect = fake_get

        videos = process_podcasts.get_new_videos("UC1234567890", {"already-there"})
        ids = [v["id"] for v in videos]
        self.assertEqual(ids, ["short-1", "regular-1"])


if __name__ == "__main__":
    unittest.main()