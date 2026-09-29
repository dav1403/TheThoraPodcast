"""Espace rav — CLI: collect listening stats, publish encrypted per-rav reports.

    python scripts/stats_update.py collect [--days 30]
        Fetch the last complete days from every configured source and merge
        them into the encrypted history  R2 _stats/history.bin.
    python scripts/stats_update.py publish
        Build one report per rav (channels + guests), encrypt it with the
        rav's token and upload it to R2 _stats/pub/<id>.bin (stale blobs of
        a rotated key are removed).
    python scripts/stats_update.py pull --dest site/espace-rav/d
        Copy the published blobs into a built site (deploy-site.yml).
    python scripts/stats_update.py links            (LOCAL ONLY)
        Print the private link of every rav — refuses to run in Actions,
        whose logs are public.
    python scripts/stats_update.py import-platforms --csv FILE   (LOCAL ONLY)
        Merge figures read in the Spotify / Apple / Deezer consoles
        (columns: slug,platform,start,end,plays).
    python scripts/stats_update.py show --slug SLUG                (LOCAL ONLY)

Environment: STATS_MASTER_KEY + the R2 secrets of the pipeline; optional
sources: CF_API_TOKEN (+ CF_ACCOUNT_ID), GA4_SA_JSON (+ GA4_PROPERTY_ID).
See stats_core.py for the confidentiality model. Logs only carry counts of
rows / days / files, never a figure of a rav nor a token.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats_core as core  # noqa: E402

STATS_PREFIX = os.environ.get("TTP_STATS_PREFIX", "_stats/")
HISTORY_KEY = STATS_PREFIX + "history.bin"
PUB_PREFIX = STATS_PREFIX + "pub/"
STATE_PREFIX = os.environ.get("TTP_STATE_PREFIX", "_state/")
ROOT = Path(__file__).resolve().parent.parent


def _summary(lines: list[str]) -> None:
    text = "\n".join(lines)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _local_only(cmd: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI"):
        raise SystemExit(f"`{cmd}` prints private data: local use only (Actions logs are public)")


def _master() -> bytes:
    try:
        return core.load_master_key(os.environ.get("STATS_MASTER_KEY"))
    except ValueError as exc:
        raise SystemExit(f"::error::{exc} — set the STATS_MASTER_KEY secret") from None


def _r2():
    from site_state import r2_client
    return r2_client(workers=8)


def _is_missing(exc: Exception) -> bool:
    s = str(exc)
    return "NoSuchKey" in s or "404" in s or "Not Found" in s


def load_history(s3, bucket: str, master: bytes) -> dict:
    try:
        obj = s3.get_object(Bucket=bucket, Key=HISTORY_KEY)
    except Exception as exc:
        if _is_missing(exc):
            return core.empty_history()
        raise                      # never start over from empty on a read error
    return core.open_history(master, obj["Body"].read())


def save_history(s3, bucket: str, master: bytes, history: dict) -> None:
    s3.put_object(Bucket=bucket, Key=HISTORY_KEY, Body=core.seal_history(master, history),
                  ContentType="application/octet-stream", CacheControl="no-store")


# --------------------------------------------------------------------------

def cmd_collect(args) -> int:
    import stats_sources as src
    master = _master()
    s3, bucket = _r2()
    history = load_history(s3, bucket, master)
    today = date.today() if not args.today else date.fromisoformat(args.today)
    end = today - timedelta(days=1)
    start = end - timedelta(days=args.days - 1)
    days = [start + timedelta(days=i) for i in range(args.days)]
    lines = [f"### Espace rav — collecte {start} → {end}", ""]
    active = 0

    try:
        direct = src.fetch_r2_downloads(days)
        core.merge_daily(history, "direct", direct)
        lines.append(f"- Téléchargements R2 (Cloudflare) : **actif** — {len(direct)} jours, "
                     f"{sum(len(v) for v in direct.values())} lignes épisode×jour")
        active += 1
    except src.SourceUnavailable as exc:
        lines.append(f"- Téléchargements R2 (Cloudflare) : inactif ({exc})")
    except Exception as exc:  # a broken source must not block the others
        lines.append(f"- Téléchargements R2 (Cloudflare) : **ERREUR** {str(exc)[:300]}")
        print(f"::error::Cloudflare source failed: {str(exc)[:300]}")
        args._failed = True

    try:
        page, ep, note = src.fetch_ga4(start, end)
        core.merge_daily(history, "site_page", page)
        if ep is not None:
            core.merge_daily(history, "site_ep", ep)
        lines.append(f"- Écoutes site (GA4) : **actif** — {len(page)} jours ; {note}")
        active += 1
    except src.SourceUnavailable as exc:
        lines.append(f"- Écoutes site (GA4) : inactif ({exc})")
    except Exception as exc:
        lines.append(f"- Écoutes site (GA4) : **ERREUR** {str(exc)[:300]}")
        print(f"::error::GA4 source failed: {str(exc)[:300]}")
        args._failed = True

    n_platform = len(history.get("platforms", []))
    lines.append(f"- Plateformes (relevés manuels Spotify/Apple/Deezer) : {n_platform} relevé(s) en historique")
    if active:
        save_history(s3, bucket, master, history)
        lines.extend(["", "Historique chiffré mis à jour dans R2."])
    else:
        lines.extend(["", "Aucune source automatique configurée : historique inchangé."])
        print("::warning::Espace rav: no automatic source configured yet "
              "(CF_API_TOKEN / GA4_SA_JSON missing) — reports carry no figure")
    _summary(lines)
    return 1 if getattr(args, "_failed", False) else 0


def load_catalogue(s3, bucket: str) -> dict:
    channels = json.loads((ROOT / "channels.json").read_text(encoding="utf-8"))
    speakers = json.loads((ROOT / "speakers.json").read_text(encoding="utf-8"))
    entries = {}
    for slug in [c["slug"] for c in channels] + [s["slug"] for s in speakers]:
        try:
            obj = s3.get_object(Bucket=bucket, Key=f"{STATE_PREFIX}feeds/{slug}.entries.json")
            entries[slug] = json.loads(obj["Body"].read().decode("utf-8"))
        except Exception as exc:
            if not _is_missing(exc):
                raise
            entries[slug] = []
    return core.build_catalogue(channels, speakers, entries)


def cmd_publish(args) -> int:
    master = _master()
    s3, bucket = _r2()
    history = load_history(s3, bucket, master)
    catalogue = load_catalogue(s3, bucket)
    today = date.today() if not args.today else date.fromisoformat(args.today)
    wanted = {}
    for slug, rav in sorted(catalogue.items()):
        token = core.rav_token(master, slug)
        bid = core.blob_id(token)
        report = core.build_report(rav, catalogue, history, today)
        s3.put_object(Bucket=bucket, Key=f"{PUB_PREFIX}{bid}.bin", Body=core.seal_report(token, report),
                      ContentType="application/octet-stream", CacheControl="no-cache")
        wanted[bid] = slug
    stale = [k for k in _list(s3, bucket, PUB_PREFIX) if k[len(PUB_PREFIX):-4] not in wanted]
    for k in stale:
        s3.delete_object(Bucket=bucket, Key=k)
    with_eps = sum(1 for r in catalogue.values() if r["episodes"])
    _summary([f"### Espace rav — publication", "",
              f"- {len(wanted)} rapports chiffrés publiés ({with_eps} ravs avec épisodes connus)",
              f"- {len(stale)} rapport(s) périmé(s) supprimé(s)"])
    if with_eps == 0:
        print("::error::no episode found in R2 _state/feeds — catalogue empty")
        return 1
    return 0


def _list(s3, bucket: str, prefix: str) -> list[str]:
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".bin")]
    return keys


def cmd_pull(args) -> int:
    s3, bucket = _r2()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for key in _list(s3, bucket, PUB_PREFIX):
        name = key[len(PUB_PREFIX):]
        if "/" in name or not all(c in "0123456789abcdef" for c in name[:-4]):
            continue
        (dest / name).write_bytes(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        n += 1
    print(f"espace-rav: {n} encrypted report(s) copied to {dest}")
    return 0


def cmd_links(args) -> int:
    _local_only("links")
    master = _master()
    channels = json.loads((ROOT / "channels.json").read_text(encoding="utf-8"))
    speakers = json.loads((ROOT / "speakers.json").read_text(encoding="utf-8"))
    rows = [(c["slug"], c.get("podcast_author", "")) for c in channels if c.get("enabled", True)]
    rows += [(s["slug"], s.get("name", "")) for s in speakers]
    lines = [f"{slug}\t{name}\t{core.rav_link(core.rav_token(master, slug))}"
             for slug, name in rows if not args.slug or slug == args.slug]
    if args.out:                   # UTF-8 file (Windows consoles are not)
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{len(lines)} link(s) written to {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print("\n".join(lines))
    return 0


def cmd_show(args) -> int:
    _local_only("show")
    master = _master()
    s3, bucket = _r2()
    token = core.rav_token(master, args.slug)
    obj = s3.get_object(Bucket=bucket, Key=f"{PUB_PREFIX}{core.blob_id(token)}.bin")
    print(json.dumps(core.open_report(token, obj["Body"].read()), ensure_ascii=False, indent=2))
    return 0


def cmd_import_platforms(args) -> int:
    _local_only("import-platforms")
    master = _master()
    s3, bucket = _r2()
    with open(args.csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    history = load_history(s3, bucket, master)
    n = core.add_platform_rows(history, rows)
    save_history(s3, bucket, master, history)
    print(f"{n} platform row(s) merged; run `publish` to refresh the reports")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--today", help="override today's date (YYYY-MM-DD), tests")
    c.set_defaults(fn=cmd_collect)
    c = sub.add_parser("publish")
    c.add_argument("--today")
    c.set_defaults(fn=cmd_publish)
    c = sub.add_parser("pull")
    c.add_argument("--dest", required=True)
    c.set_defaults(fn=cmd_pull)
    c = sub.add_parser("links")
    c.add_argument("--slug")
    c.add_argument("--out", help="write the links to this UTF-8 file instead of stdout")
    c.set_defaults(fn=cmd_links)
    c = sub.add_parser("show")
    c.add_argument("--slug", required=True)
    c.set_defaults(fn=cmd_show)
    c = sub.add_parser("import-platforms")
    c.add_argument("--csv", required=True)
    c.set_defaults(fn=cmd_import_platforms)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
