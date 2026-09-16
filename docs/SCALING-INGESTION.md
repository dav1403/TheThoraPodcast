# Scaling ingestion — cookie pool and bursts

This project downloads YouTube audio in CI, on a single GitHub-hosted lane
(`.github/workflows/repair_missing_audio.yml`, displayed as "Update Podcast
Feeds", cron `0 * * * *`). As we add channels, one cookie from one account gets
rate-limited/blocked. This guide covers the levers that scale ingestion:

1. A **rotating cookie pool** (many YouTube accounts, one used per run).
2. ~~A second self-hosted lane on David's PC~~ — **retired 2026-09-16**, kept
   below only so the decision is not re-litigated.
3. **Bursts** to drain the backlog of a freshly added channel.

---

## 1. Cookie pool — `YOUTUBE_COOKIES_POOL`

### Why

Rotating cookies spreads download load across many accounts, so no single
account looks like a bot hammering YouTube. Each run picks one cookie from the
pool by rotating on the run number, so consecutive runs use different accounts.
A per-lane rotation offset also exists, so that two lanes would never hit
YouTube as the same identity in the same slot; with a single lane today the
hosted lane simply uses `OFFSET=0`.

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

## 2. Self-hosted lane — RETIRED on 2026-09-16

**There is no second lane. `.github/workflows/repair_selfhosted.yml` has been
deleted; do not re-create it.** This section is kept only so the decision is not
re-litigated.

A second ingestion lane ("Update Podcast Feeds (self-hosted)", `runs-on:
[self-hosted, ttp]`, cron `30 * * * *`, budget 60, cookie `OFFSET=1`) once
existed to download from a residential IP. It was **disabled on 2026-08-05** and
deleted on 2026-09-16, for three reasons:

- **It never ran, not once.** No runner labelled `ttp` was ever registered
  (`gh api repos/dav1403/TheThoraPodcast/actions/runners` → `total_count: 0`).
  Every hourly run sat queued and was then cancelled, which read as a wall of
  failures.
- **A self-hosted runner would execute on David's own PC**, which contradicts the
  standing rule that everything runs in the cloud. So the lane could never be
  switched on as designed — leaving the file in place promised capacity that was
  never going to arrive.
- **The hosted lane covers the load alone.** It has been green on every scheduled
  run checked on 2026-09-16.

The file had also drifted: it predated the ingestion watchdog and never carried
those steps, so reviving it would have restored an *unmonitored* lane.

Nothing unique was lost. The cookie-pool rotation (section 1) lives in
`scripts/select_cookie_from_pool.py` and is wired into the hosted lane with
`OFFSET=0`; the offset mechanism still works if a second consumer ever appears.

Recovering the file, should the constraint ever change, is a single command:
`git show <commit-before-deletion>^:.github/workflows/repair_selfhosted.yml`.

<details>
<summary>What the retired lane contained</summary>

It was a near-copy of the hosted workflow with:

- `runs-on: [self-hosted, ttp]`,
- cron `30 * * * *` (staggered 30 min from the hosted `0 * * * *`),
- `PROCESS_BUDGET_DEFAULT: 60` (residential IPs tolerate heavier runs),
- cookie `OFFSET=1` (different account than the hosted lane at the same slot),
- a distinct concurrency group (`podcast-pipeline-selfhosted`),
- a `git pull --rebase` + retry loop before push (both lanes push to `feeds/`).

It stayed inert for its whole life: the workflow existed but had nowhere to run.

Its prerequisites, had a runner ever been registered, were system-installed
`git`, Python 3.11+, `ffmpeg` and Deno on the host machine, plus a reusable venv
at `~/.ttp-venv`. The runner registration procedure is standard GitHub
documentation and is deliberately not reproduced here any more.

</details>

---

## 3. Bursts — draining a new channel's backlog

When a new channel is added it has a large backfill backlog. To drain it fast,
**manually dispatch the hosted lane with a raised budget**:

- GitHub UI: **Actions → "Update Podcast Feeds" → Run workflow →** set
  `process_budget` above the default 30.
- Or CLI:

  ```bash
  gh workflow run repair_missing_audio.yml -f process_budget=60
  ```

There is no dedicated burst cron — a burst is just a high-budget manual dispatch.
The regular `:00` cron keeps running at budget 30 in between.

⚠️ **Do not raise the budget without watching the clock.** The hosted lane's
`podcast-pipeline` concurrency group *queues* an overrun behind the next hourly
cron, and GitHub then drops scheduled runs. A run that stretches past ~50 min
costs runs/day and can cancel out the gain. Check the duration of the burst run
before repeating it. (Earlier guidance sent bursts to the self-hosted lane at
budget 100–150; that lane no longer exists, and those numbers were sized for a
residential IP with its own concurrency group — they do **not** transfer to the
hosted lane.)

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

There is only one lane since 2026-09-16 (see section 2).

- Nominal ≈ **~720 items/day**, but the budget is a *ceiling*: a run stops early
  once there's nothing left to fetch, and long runs can be dropped by the
  concurrency queue. Treat this as an upper bound, not a measurement.
- The pool of **N cookies** spreads that load across N accounts, so per-account
  daily volume ≈ `items / N`. More cookies ⇒ lower per-account risk of
  throttling ⇒ you can safely raise the budget.
- **Bursts** (section 3) add one-off spikes on top, best used right after adding
  a channel to clear its backlog.

To scale further: add cookies to the pool first (lowers per-account risk), then
raise `PROCESS_BUDGET_DEFAULT` — but keep runs under ~50 min (see the budget
comment in `repair_missing_audio.yml`) so scheduled runs aren't dropped. That
ceiling is now the binding one: with the second lane gone, there is no other
place to put load except more cookies and a longer, riskier run.
