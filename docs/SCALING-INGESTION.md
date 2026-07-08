# Scaling ingestion — cookie pool, self-hosted lane, and bursts

This project downloads YouTube audio in CI. As we add channels, one cookie from
one account gets rate-limited/blocked. This guide covers the three levers that
scale ingestion:

1. A **rotating cookie pool** (many YouTube accounts, one used per run).
2. A **second self-hosted lane** running on David's PC (residential IP).
3. **Bursts** to drain the backlog of a freshly added channel.

---

## 1. Cookie pool — `YOUTUBE_COOKIES_POOL`

### Why

Rotating cookies spreads download load across many accounts, so no single
account looks like a bot hammering YouTube. The hosted lane and the self-hosted
lane pick *different* cookies at the same time slot (via a rotation offset), so
they never hit YouTube as the same identity simultaneously.

### Format

`YOUTUBE_COOKIES_POOL` is a GitHub **repository secret** containing several
Netscape cookie files **concatenated**, each separated by a line that is
**exactly**:

```
-----COOKIE-----
```

A single Netscape cookie file already contains tabs and newlines, so a plain
newline can't be the separator — the sentinel line is used because it never
appears inside a real cookie file. Leading/trailing blank lines around the
sentinel are fine.

Example with **3** accounts (each block is a full `cookies.txt` export):

```
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1799999999	SID	<account-1-value>
.youtube.com	TRUE	/	TRUE	1799999999	HSID	<...>
-----COOKIE-----
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1799999999	SID	<account-2-value>
.youtube.com	TRUE	/	TRUE	1799999999	HSID	<...>
-----COOKIE-----
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1799999999	SID	<account-3-value>
.youtube.com	TRUE	/	TRUE	1799999999	HSID	<...>
```

### How selection works

The `Write YouTube cookies` step runs `scripts/select_cookie_from_pool.py`,
which:

- splits `YOUTUBE_COOKIES_POOL` on the sentinel line into N cookies,
- picks index `(github.run_number + OFFSET) % N`
  (hosted lane `OFFSET=0`, self-hosted lane `OFFSET=1`),
- writes the chosen cookie to `/tmp/yt_cookies.txt` (consumed by the scripts via
  `YOUTUBE_COOKIES_FILE`),
- logs only the chosen **index** and pool size — never the cookie content.

**Fallback / backward compatibility:** if `YOUTUBE_COOKIES_POOL` is absent,
empty, or malformed (no valid cookie parses), the script falls back to the
existing single `YOUTUBE_COOKIES` secret and emits a `::warning::`. If neither is
set, the run proceeds without cookies (unchanged behaviour). So you can adopt the
pool with zero risk: set `YOUTUBE_COOKIES_POOL` and keep `YOUTUBE_COOKIES` as a
safety net.

### Populating the secret

Export `cookies.txt` for each account (e.g. the "Get cookies.txt LOCALLY"
browser extension, logged into that YouTube account), then join them with the
sentinel line and store the result as the `YOUTUBE_COOKIES_POOL` repo secret:

```bash
# One file per account: cookies1.txt cookies2.txt cookies3.txt ...
{ cat cookies1.txt; for f in cookies2.txt cookies3.txt; do printf '\n-----COOKIE-----\n'; cat "$f"; done; } > pool.txt
gh secret set YOUTUBE_COOKIES_POOL < pool.txt
rm pool.txt   # never commit real cookies
```

> **Never commit real cookies.** The pool lives only in the GitHub secret.

**Refreshing:** YouTube cookies expire. The refresh script on the Raspberry Pi
(or wherever cookies are re-minted) must update **this** `YOUTUBE_COOKIES_POOL`
secret — i.e. re-export each account, re-concatenate with the sentinel, and
`gh secret set YOUTUBE_COOKIES_POOL`. Refreshing only the old single
`YOUTUBE_COOKIES` secret has no effect once the pool is in use (the pool wins).

### Test

```bash
bash scripts/tests/test_select_cookie_from_pool.sh
```

Covers rotation across run numbers, the offset lane picking a different cookie,
and fallback to the single secret on an empty/malformed pool.

---

## 2. Self-hosted lane — David's PC (residential IP)

`.github/workflows/repair_selfhosted.yml` ("Update Podcast Feeds (self-hosted)")
is a near-copy of the hosted workflow with:

- `runs-on: [self-hosted, ttp]`,
- cron `30 * * * *` (staggered 30 min from the hosted `0 * * * *`),
- `PROCESS_BUDGET_DEFAULT: 60` (residential IPs tolerate heavier runs),
- cookie `OFFSET=1` (different account than the hosted lane at the same slot),
- a distinct concurrency group (`podcast-pipeline-selfhosted`),
- a `git pull --rebase` + retry loop before push (both lanes push to `feeds/`).

It stays **inert** until a runner labelled `ttp` is registered — the workflow
exists but has nowhere to run, so nothing happens on David's account.

### Prerequisites on David's PC

The self-hosted lane uses **system-installed** tooling (not the `setup-*`
actions). Install on the machine that will host the runner (Linux / macOS / WSL —
the workflow steps are bash-based):

- **git**
- **Python 3.11+** (`python3` on PATH)
- **ffmpeg** (`ffmpeg` on PATH) — e.g. `sudo apt-get install ffmpeg` / `brew install ffmpeg`
- **Deno** (`deno` on PATH) — yt-dlp's EJS n-challenge solver:
  `curl -fsSL https://deno.land/install.sh | sh`

yt-dlp, boto3, feedgen, Pillow, anthropic, etc. are installed automatically by
the workflow into a reusable venv at `~/.ttp-venv` (from `requirements.txt`).

### Install the runner

1. On GitHub: **repo → Settings → Actions → Runners → New self-hosted runner**.
   Copy the registration token it shows (a fresh `RUNNER_TOKEN`).
2. On David's PC:

   ```bash
   mkdir actions-runner && cd actions-runner
   # Download the runner package GitHub shows on that page (curl command varies by OS/arch), then:
   ./config.sh --url https://github.com/dav1403/TheThoraPodcast --token <RUNNER_TOKEN> --labels ttp
   ```

   The `--labels ttp` is what makes the workflow's `runs-on: [self-hosted, ttp]`
   match. (`self-hosted` is added automatically.)
3. Run it:

   ```bash
   ./run.sh
   ```

   or install it as an always-on service so it survives reboots:

   ```bash
   sudo ./svc.sh install
   sudo ./svc.sh start
   ```

Once the runner shows **Idle** in GitHub, the `:30` cron (and any manual
dispatch) starts executing on David's PC. To pause the lane, stop the runner
(`./svc.sh stop` or Ctrl-C on `./run.sh`); the workflow goes inert again.

---

## 3. Bursts — draining a new channel's backlog

When a new channel is added it has a large backfill backlog. To drain it fast,
**manually dispatch the self-hosted lane with a high budget** (its residential IP
absorbs it better than the hosted datacenter IP):

- GitHub UI: **Actions → "Update Podcast Feeds (self-hosted)" → Run workflow →**
  set `process_budget` to **100–150**.
- Or CLI:

  ```bash
  gh workflow run repair_selfhosted.yml -f process_budget=150
  ```

There is no dedicated burst cron — a burst is just a high-budget manual dispatch.
The regular `:30` cron keeps running at budget 60 in between.

---

## 4. Throughput math

Rough capacity (items/day) is:

```
lanes × runs_per_hour × 24 × budget_per_run
```

but the real ceiling is **cookies × per-account YouTube tolerance**, not compute.

| Lane        | Cron        | Runs/day | Budget/run | Nominal items/day |
|-------------|-------------|----------|------------|-------------------|
| Hosted      | `0 * * * *` | ~24      | 30         | ~720              |
| Self-hosted | `30 * * * *`| ~24      | 60         | ~1440             |

- Nominal combined ≈ **~2000 items/day**, but budgets are *ceilings*: a run stops
  early once there's nothing left to fetch, and long runs can be dropped by the
  concurrency queue.
- The pool of **N cookies** spreads that load across N accounts, so per-account
  daily volume ≈ `combined_items / N`. More cookies ⇒ lower per-account risk of
  throttling ⇒ you can safely raise budgets.
- **Bursts** (dispatch self-hosted at 100–150) add one-off spikes on top, best
  used right after adding a channel to clear its backlog.

To scale further: add cookies to the pool first (lowers per-account risk), then
raise `PROCESS_BUDGET_DEFAULT` — but keep the hosted lane's runs under ~50 min
(see the budget comment in `repair_missing_audio.yml`) so scheduled runs aren't
dropped. The self-hosted lane, being alone in its concurrency group with a 30-min
stagger, has more headroom for a higher budget.
