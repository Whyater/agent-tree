<p align="center">
  <img src="assets/icon-1024.png" width="96" height="96" alt="Agent Tree icon">
</p>

<h1 align="center">Agent Tree</h1>

<p align="center">
  See where your AI agent compute goes.<br>
  Rebuilds the tree of agents you spawn in Claude Code, Codex and T3 Code from the transcripts already on your Mac,<br>
  then shows who spawned whom, which model ran, for how long, how many tokens it used and roughly what it cost.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#where-the-data-comes-from">Data sources</a> ·
  <a href="#privacy">Privacy</a> ·
  <a href="#costs">Costs</a> ·
  <a href="#flags">Flags</a> ·
  <a href="#limits">Limits</a>
</p>

<p align="center">
  <img src="docs/screenshots/thread-dark.png" alt="Thread view: an orchestrator with nested subagents, aligned with a timeline" width="900">
</p>

---

I run a lot of orchestrator setups: one Claude session that hands work to
subagents, some of which hand work to Codex, which spawns its own workers. After
a few weeks I had no idea where the tokens were going, which runs went five
levels deep, or whether my "use the cheap model for scans" rule was actually
being followed. The data was all on disk already, spread over JSONL transcripts
and a couple of SQLite databases. Agent Tree reads it (read-only), stitches it
back into trees, and puts a dashboard on top.

It runs entirely on your machine. Nothing is uploaded, there's no account, and
it never writes to the folders it reads.

## Install

Pick one. None of them need `sudo`.

**1. Mac app (easiest).** Download `AgentTree-<version>-arm64.dmg` (Apple
silicon) or `-x86_64.dmg` (Intel) from the
[latest release](https://github.com/Whyater/agent-tree/releases/latest), open it
and drag **Agent Tree** to Applications. It bundles its own Python, so there is
nothing else to install. Opening the app starts a local server and opens the
dashboard in your browser; quitting the app stops it.

> The app is signed ad-hoc, not notarized (that needs a paid Apple developer
> account). The first time, macOS will say it can't verify the developer. Open
> **System Settings → Privacy & Security**, scroll down and click **Open Anyway**.
> You only do this once. If you'd rather skip that, use option 2.

**2. One command.** Installs the `att` command into `~/.local`:

```sh
curl -fsSL https://raw.githubusercontent.com/Whyater/agent-tree/main/install.sh | sh
att open
```

It uses your Python if it's 3.11 or newer, and otherwise downloads a private
standalone Python (about 25 MB) next to the app, leaving your system alone.
`install.sh --uninstall` removes it again.

**3. From a clone.** Agent Tree has no dependencies, so this just works:

```sh
git clone https://github.com/Whyater/agent-tree.git
cd agent-tree
python3 -m agent_tree open        # or ./install.sh to get the `att` command
```

## What you get

**Home.** Tokens and estimated cost for today, the last 7 days and the last 30,
split by model family and model, with a per-day chart, the ten most expensive
threads and a count of open flags.

**Threads.** Every top-level session across every project, newest first.
Filter by project, source, model, date range or "has flags", and search titles.

**Thread view.** The main screen. A collapsible tree of every agent in the
thread, with model, depth, duration, tokens, estimated cost and status, next to
a timeline that lines up row for row so you can see what ran in parallel and
where things sat idle. Toggle between a node's own numbers and its whole
subtree. Select a node to see everything known about it, including the exact
source file and byte offset it came from and how confident the parent link is.

**Projects and Models.** Compute per project over time, and per model and
family, including a rough split by task type (build, review, research, scan,
design, test).

**Flags.** Rules you can tune, such as trees deeper than four levels, more than
20 agents at once, a blocked model being used, an expensive model on a trivial
scan, or a reviewer from the same model family as the builder it reviewed.

**Export.** Any thread as JSON, CSV or a Markdown tree you can paste into notes,
and any date range as JSON or CSV.

<p align="center">
  <img src="docs/screenshots/home-dark.png" alt="Home: totals by period, family and model, daily chart, top threads" width="900">
</p>

### Keyboard

| Key | Action |
|---|---|
| `j` / `k` | move down / up |
| `Enter` | expand or collapse, open a thread |
| `/` | search |
| `t` | toggle the timeline |
| `f` | flags |
| `g` then `h` `t` `p` `m` `f` | go to Home, Threads, Projects, Models, Flags |
| `?` | all shortcuts |

### Command line

The `att` command does everything the dashboard does, which is handy over SSH or
in scripts.

```text
att open                          start the dashboard and open it in a browser
att serve [--port 8711]           start the dashboard without opening a browser
att scan [--full]                 index new data (incremental; --full rebuilds)
att threads [--project X --since 7d --source codex --flagged]
att tree <thread-id> [--markdown] [--subtree]
att top --by cost|tokens|time [--since 7d]
att flags [--since 7d]
att export <thread-id> --format json|csv|md
att export --since 7d --format csv
att doctor                        check sources, parser health and settings
att config [--reset prices.toml]  show or reset the settings files
```

Thread ids accept any node id in the tree, a bare session id, or a unique prefix.

```text
$ att tree 6d4f --subtree
Ship the billing export
cc:6d4f4448-…  ·  ~/Projects/billing  ·  t3 → claude-code → codex
9 agents, depth 3, 41m 07s, 6.8M tokens, est. $9.12

Ship the billing export · claude-opus-5-5 · 41m 07s · 6.8M tok · $9.12
├─ Plan the export format · claude-sonnet-5-5 · 2m 10s · 310k tok · $0.41
├─ Sol: build CSV writer · claude-haiku-5-5 · 14m 02s · 2.9M tok · $1.37
│  └─ codex-agent run · gpt-6.1-sol · 13m 40s · 2.8M tok · $1.31
│     ├─ /root/tests · gpt-6-luna · 4m 12s · 420k tok · $0.05
│     └─ /root/ui_polish · gpt-6-luna · 3m 05s · 380k tok · $0.04 · ⚑ openai_on_ui
└─ Critic: review CSV writer · claude-opus-5-5 · 6m 31s · 1.1M tok · $2.20
```

## Where the data comes from

Agent Tree reads these locations and nothing else. Every one is opened
read-only, and the live SQLite database is opened in a way that takes no locks.

| Source | What's read | What it gives |
|---|---|---|
| **Claude Code** `~/.claude/projects/<project>/<session>.jsonl` | assistant records: model, `usage`, `requestId`, timestamps, `cwd`; `Agent`/`Task` tool calls (id, description, requested model) and their results (agent id, status) | the main session, its tokens, and which tool call spawned each subagent |
| **Claude Code subagents** `…/<session>/subagents/agent-<id>.jsonl` + `.meta.json` | same as above, plus the meta file's `toolUseId`, `spawnDepth`, `agentType`, `description` | every subagent, nested ones included, linked to the exact call that spawned it |
| **Codex** `~/.codex/sessions/**/rollout-*.jsonl` | `session_meta` (thread id, parent thread, agent path, cwd), `turn_context` (model, effort), `token_count` totals, task start/complete times | Codex threads and their subagents, with tokens and timing |
| **Codex titles** `~/.codex/session_index.jsonl` | thread names | titles |
| **T3 Code** `~/.t3/userdata/state.sqlite` | threads, projects, turns, provider session ids, task activity metadata | the thread title you see in T3, joined to the Claude or Codex session underneath |
| **Wrapper runs** `/tmp/codex-tasks/*.md` siblings (`.log`, `.out` presence, optional `.meta.json`) | the `codex exec` header (session id, model) and file times | Claude → Codex hand-offs made through a wrapper script, attached under the subagent that ran them |
| **Hooks (optional)** `~/.agent-tree/events.jsonl` | `SubagentStart`/`SubagentStop` ids and timestamps | exact "still running" status for live sessions |

Rescans are incremental: each file's size, modification time, inode and byte
offset are stored, and only complete new lines are read. A file that shrinks or
is replaced is parsed again from the start. Lines that don't parse are counted
and reported by `att doctor`, never fatal.

### How links are made, and how sure they are

Every parent link in the tree is labelled:

- **exact**: matched on an id that both sides record. A Claude subagent's
  `toolUseId` equals the `id` of the `Agent` call in its parent's transcript; a
  Codex child names its `parent_thread_id`; a T3 thread id is
  `import:claudeAgent:<session>`; a wrapper's task file path appears in the
  Bash call that ran it.
- **inferred**: matched by something weaker, such as a time window and working
  directory. Shown with a marker in the UI and in exports.
- **orphan**: the parent could not be found (for example its transcript was
  deleted). Orphans still show up, under the session root when it exists, and
  raise a flag.

Things that are **inferred or unconfirmed** today (full rules in
[docs/LINKING.md](docs/LINKING.md)):

- **T3 thread → Claude session, when T3 has no session id for it.** Current T3
  versions record one (in the resume cursor), so this fallback, which matches
  one session in the same workspace and time window, is rarely needed.
- **T3 task rows → Codex child threads.** Exact when the task id is a Codex
  thread id with a rollout on disk; otherwise the T3 row is shown only if the
  thread has no transcript behind it.
- **Wrapper run → Codex session without a log header.** Falls back to a
  120-second start window in the same directory, labelled inferred.
- **Claude subagents whose spawning call is missing** attach to the session root
  as inferred (depth 1) or orphan (deeper).
- **"Running"** means "touched in the last 10 minutes" unless the optional hooks
  are on.

## Privacy

- **Read-only.** Nothing under `~/.claude`, `~/.codex` or `~/.t3` is written,
  moved, locked or deleted. Databases are opened with `mode=ro&immutable=1`, or
  copied to a private temp folder when they have pending write-ahead-log data.
- **No secrets.** Files that look like credentials (`secrets/`, `auth*.json`,
  `*token*.json`, `.env*`, keys) are never opened. Any stored text is scanned for
  API-key, token, JWT and private-key patterns, which are replaced with
  `[redacted]` before anything is saved.
- **No message bodies.** Prompts, replies, tool inputs and tool outputs are never
  read into the index. What's kept is metadata plus short descriptions: the
  `description` of an `Agent` call, a Codex agent path, a thread title.
- **160 characters, at most.** Every stored description or title is cut to 160
  characters (after redaction). Set `metadata_only = true` in
  `~/.agent-tree/config.toml` to store no free text at all, then run
  `att scan --full`.
- **Local only.** The server listens on `127.0.0.1`, rejects requests whose Host
  header isn't local, and requires a custom header for the one write endpoint
  (rescan). There is no telemetry and no network access of any kind; prices come
  from a local file.
- Everything Agent Tree writes lives in `~/.agent-tree/`. Delete that folder to
  remove every trace.

## Costs

Costs are **estimates** everywhere they appear. They come from
`~/.agent-tree/prices.toml`, which is created on first run and is yours to edit:

```toml
[models."claude-opus-5-5"]
input = 4.00          # $ per 1M uncached input tokens
cached_input = 0.20   # cache reads
cache_write = 5.00    # cache creation (Anthropic)
output = 20.00
aliases = ["opus"]
as_of = "2026-10-12"
source = "anthropic.com/pricing"

[models."gpt-6.1-sol"]
input = 2.00
cached_input = 0.10
output = 10.00
long_context_threshold = 272000   # optional higher tier above this prompt size
long_input = 4.00
long_output = 15.00
```

- A model that isn't in the file shows **?** for cost. Its tokens still count.
- Prices are re-applied on every scan, so editing the file and rescanning (or
  clicking **Scan now**) updates every number.
- If a plan or promotion covers a provider, set it in `config.toml`:

  ```toml
  [covered_by_plan]
  openai = true
  ```

  Tokens still show; the dollar figure is greyed out and kept out of totals.
- OpenAI reports cached tokens inside `input_tokens`; Agent Tree splits them so
  "input" always means uncached input for both providers. Reasoning tokens are
  part of output, as they're billed.
- `att config --reset prices.toml` restores the bundled prices (your old file is
  kept as `prices.toml.bak`).

## Flags

Rules live in `~/.agent-tree/rules.toml`. Each can be switched off or tuned:

| Rule | Default | Kind |
|---|---|---|
| `depth` | tree deeper than 4 (root is 0) | exact |
| `concurrency` | more than 20 agents running at once in one tree | exact |
| `blocked_model` | any model on your blocked list (default: `gpt-6-astra`) | exact |
| `orphan` | parent not found | exact |
| `long_running` | a subagent ran more than 30 min | exact |
| `heavy_tokens` | a subagent used more than 2M tokens, not counting cache reads | exact |
| `openai_on_ui` | an OpenAI model on a task whose description mentions UI, UX, design, layout, CSS… | heuristic |
| `same_family_critic` | a reviewer from the same model family as the builder it likely reviewed | heuristic |
| `expensive_model_trivial_task` | Opus, Fable or Astra on a task that looks like a scan, grep or relay | heuristic |
| `duplicate_tasks` | near-identical task descriptions in the same thread | heuristic |

Heuristic flags guess intent from short descriptions, so they're labelled
"heuristic" wherever they appear. `long_running` and `heavy_tokens` skip
top-level sessions unless you set `include_roots = true`.

## Optional extras

Two small changes outside this repo make the data richer. Neither is applied
for you; each is written up as an exact diff:

- [**Claude Code hooks**](docs/proposals/claude-hooks.md): exact running/finished
  status for subagents while a session is live.
- [**Codex wrapper sidecar**](docs/proposals/codex-agent-wrapper.md): if you hand
  work from Claude to Codex through a `codex-agent.sh`-style wrapper, this makes
  each run write a small `.meta.json` (caller, timing, Codex session id, tokens).

## Limits

- macOS is the main target. The CLI and dashboard also run on Linux; the `.app`
  is macOS-only.
- Formats are not public APIs and can change with any Claude Code, Codex or T3
  release. Parsers are defensive and versioned (every row records the parser
  version), and `att doctor` shows skipped-line counts so drift is easy to spot.
- Costs are list-price estimates. They don't know about batch discounts,
  priority tiers, regional pricing or your actual invoice.
- Usage is attributed to the day each request happened, in your local time zone
  (override with `[display] timezone` in `config.toml`).
- "Running" is a guess (transcript touched in the last 10 minutes) unless the
  optional hooks are enabled.
- Task-type shares on the Models page come from keywords in descriptions and are
  rough.

## Development

```sh
python3 -m unittest discover -s tests -t . -v   # unit and fixture tests, no real data needed
python3 scripts/check_contrast.py          # WCAG AA check for both themes
python3 scripts/mock_server.py             # UI with synthetic data on 127.0.0.1:8799
python3 scripts/recount.py pick            # find real sessions to verify against
python3 scripts/recount.py claude <id>     # independent recount vs. the index
packaging/build_app.sh                     # build Agent Tree.app and the .dmg
```

The code is small and has no dependencies:

```text
agent_tree/
  sources/      one parser per data source, plus shared incremental reading
  indexer.py    links parents, assigns depth, rolls up totals, runs flags
  queries.py    everything the API and CLI show
  server.py     127.0.0.1 server for the UI and JSON API (docs/API.md)
  web/          the dashboard: plain HTML, CSS and JavaScript, no build step
```

[DECISIONS.md](DECISIONS.md) records the design choices and why they were made.

## License

MIT
