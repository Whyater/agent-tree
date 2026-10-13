# Decisions

One line of reasoning each. Newest at the bottom of each section.

## Product

- **Local web UI, not SwiftUI.** One Python codebase serves the CLI, API and UI; it runs anywhere Python does; a native app would duplicate every query in Swift for little gain.
- **No third-party dependencies.** Python 3.11+ standard library only (`sqlite3`, `tomllib`, `http.server`, `zoneinfo`), so installing never needs pip, a compiler or network access to PyPI.
- **Name: Agent Tree, command `att`.** Short, says what it does, and `att` doesn't collide with common tools.
- **Self-contained `.app` with a bundled Python.** "Download, drag, open" shouldn't depend on which Python a Mac happens to have; macOS's own `python3` is 3.9 behind an Xcode prompt.
- **App shell is a stay-open AppleScript applet.** It's built with `osacompile` (no Xcode), shows in the Dock, and handles Quit and reopen properly, which a bare shell-script bundle can't.
- **Ad-hoc code signing, not notarized.** Notarization needs a paid Apple developer account; the README explains the one-time "Open Anyway" step and offers the `curl | sh` route, which skips Gatekeeper entirely.
- **`curl | sh` installer writes only to `~/.local`.** Gets a working `att` without sudo, and only downloads a private Python when the system one is older than 3.11.

## Data and parsing

- **The index is a cache.** `~/.agent-tree/index.sqlite` can be deleted at any time; `att scan --full` rebuilds it, so schema changes just trigger a rebuild.
- **Parsers write facts; the indexer derives trees.** Parsers emit nodes, usage rows, spawn records and links; one resolver decides parents, depth, rollups and flags, so cross-source joins live in one place.
- **Usage is stored per billable unit with a stable key.** Claude rows are keyed by `requestId` (last write wins, which dedupes streamed blocks); Codex rows are deltas of the cumulative counter, so the sum always equals the last reported total and daily buckets stay correct.
- **Incremental reads by byte offset.** Each file's offset, size, mtime and inode are stored; only complete new lines are read, and a file that shrinks or changes inode is re-parsed from scratch.
- **Codex continuation files carry the counter on.** A long thread continues in `rollout-…-<id>_<suffix>.jsonl`; each continuation is seeded with the previous file's final totals so it stores only its own increases, keeping per-day and per-model splits right. Copies of the same history (live + archived) are deduplicated instead.
- **Small Codex counter dips are stored as small negative changes.** The cumulative counter sometimes steps back a few tokens; treating that as a reset double-counts, clamping it overshoots. Only a drop below half the previous total is a reset.
- **Codex child rollouts skip copied parent history.** Child files replay the parent's records before `subagent_history_start_ordinal`; counting them would double-count the parent.
- **OpenAI `input_tokens` includes cached tokens.** Stored as uncached `input` plus `cached_input`, so both providers share one meaning per column.
- **T3 context-window numbers are display-only.** They describe the same tokens as the underlying transcript, so adding them would double-count.
- **T3 task rows that duplicate a transcript agent are hidden.** The transcript has better data (tokens, exact model), and showing both would make the tree look twice as big.
- **Live T3 database is read via `immutable=1`, or a private copy when its WAL has data.** Immutable mode takes no locks but ignores un-checkpointed WAL pages; copying db and WAL to a temp dir gives a consistent read without touching the original.
- **`~/.codex/logs_2.sqlite` is not used.** It's a debug log whose bodies can contain prompt text, and rollouts already provide timing and tokens.
- **T3 is re-read at most every 3 minutes.** Its WAL changes constantly while the app is open and reading it means snapshotting the database; thread metadata changes slowly.
- **One scan at a time per index, across processes.** A `flock` on `~/.agent-tree/scan.lock` keeps the CLI, the server and the app from writing the same index concurrently.
- **Each scan stops at the file sizes it saw when it started.** Lines appended mid-scan wait for the next scan, so one scan is a consistent snapshot.
- **Status is recomputed on every scan.** "Running" means "transcript touched in the last 10 minutes", so it can't go stale; hook events override it when enabled.

## Links

- **Claude parent links are exact** when the subagent's `meta.toolUseId` matches a spawning `tool_use.id` (or the result's `agentId` matches).
- **Unmatched Claude subagents at depth 1 attach to the session root as "inferred"**; deeper ones attach there as "orphan" and raise the orphan flag.
- **Wrapper runs attach to the Claude subagent whose Bash call ran them (exact)**, matched by the task-file path in the transcript, and to the Codex session named in the log header (exact) or one started within 120 s in the same directory (inferred).
- **T3 threads wrap their session.** `import:claudeAgent:<id>`, `import:codex:<id>`, provider session ids and resume cursors give exact links; anything matched by time window is labelled inferred.

## Cost and flags

- **Costs are estimates from an editable `prices.toml`.** Unknown models show "?" instead of a guess; the long-context tier is applied per request.
- **"Covered by plan" is per model family.** Tokens still show; dollars move to a separate greyed "covered" figure and leave the totals.
- **Long-running and heavy-token flags skip top-level sessions by default.** An interactive session that lasts two hours is normal; a subagent that does is what's worth a look (`include_roots = true` turns it on).
- **Same-family critic rule compares a reviewer with the most recent non-reviewer sibling before it.** That's the usual "build, then review" pattern; it is labelled heuristic.
- **Duplicate-task rule compares normalized descriptions within one tree** at ≥ 0.9 similarity, capped at 400 described nodes per tree to stay fast.

## Privacy

- **At most 160 characters of any description or title, redacted before truncation.** Redacting first means a key can't survive by being cut in half.
- **Prompts, message bodies, tool inputs and outputs are never read into the index.** Only task descriptions (Agent tool `description`, Codex agent paths, thread titles) are kept.
- **Metadata-only mode stores no free text at all.**
- **The server binds to 127.0.0.1, checks the Host header and requires a custom header on POST.** This blocks DNS-rebinding and cross-site requests from other pages in the same browser.

## Proposals not applied

- **Wrapper sidecar (`docs/proposals/codex-agent-wrapper.patch`) and Claude hooks (`docs/proposals/claude-hooks.md`)** change files outside this repo, so they are written as exact diffs for the owner to apply; Agent Tree already handles both formats if they appear.
