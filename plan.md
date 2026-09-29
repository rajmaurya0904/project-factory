# PLAN: Autonomous Open-Source Project Factory

## 0. Goal

Build a runner that autonomously:
1. Finds small, useful project ideas.
2. Validates them against existing GitHub projects.
3. Creates a public GitHub repo.
4. Builds it through a loop of small tasks, one real commit per task.
5. Ships (tests, CI, README, release).
6. Logs everything and starts the next idea.

Target: 50-100 real commits/day. No fake commits (no empty commits, whitespace churn, or timestamp edits).

## 1. Assumptions (change in `config.yaml`)

- Runner language: Python 3.11+ (stdlib + `sqlite3`, `subprocess`, `pyyaml`).
- Agent driver: Claude Code headless (`claude -p`). Codex (`codex exec`) is a pluggable alternative.
- Host: Linux box (AWS), `systemd` service + timer.
- Auth: `gh` CLI authenticated with a token scoped to a dedicated GitHub account/org.
- Generated tools: Python or TypeScript, chosen per idea.
- Runner code lives in its own repo: `project-factory`.

## 2. Hard Rules

- Every commit must contain a real change: code, test, doc, CI, example, or fix.
- Reject empty diffs. Reject diffs that only touch whitespace, comments, or timestamps.
- Every commit passes the gate (Section 7) before it is created.
- No secrets in commits. Run a secret scan before every push.
- Never touch repos outside the allowlist (`factory-*` prefix or the configured org).
- Never force-push. Never delete repos. Never rewrite history.
- Respect the stop file: if `./STOP` exists, finish the current task and exit.
- Respect daily caps (commits, sessions, cost). Exit cleanly at cap.

## 3. Repo Layout (`project-factory`)

```
project-factory/
  plan.md
  config.yaml
  README.md
  pyproject.toml
  factory/
    __init__.py
    main.py            # entrypoint: orchestrates the state machine
    db.py              # SQLite access
    agent.py           # wraps claude/codex subprocess calls
    router.py          # stage/complexity -> model (haiku|sonnet), escalation
    ideate.py          # stage 1
    validate.py        # stage 2
    scaffold.py        # stage 3
    planner.py         # stage 4
    builder.py         # stage 5
    gate.py            # stage 6
    release.py         # stage 7
    guard.py           # caps, stop file, allowlist, secret scan
    log.py
  prompts/
    ideate.md
    validate.md
    plan.md
    build_task.md
    fix_gate.md
    release.md
  templates/
    python/            # pyproject, tests dir, ci.yml, ruff config
    node/              # package.json, vitest, ci.yml, eslint config
    skill/             # SKILL.md skeleton
    mcp/               # MCP server skeleton
  state/
    factory.db
    logs/
  systemd/
    factory.service
    factory.timer
  tests/
```

## 4. Configuration (`config.yaml`)

```yaml
agent:
  driver: claude            # claude | codex
  models:
    haiku: haiku            # cheap: mechanical work
    sonnet: sonnet          # strong: reasoning and core code
  stage_models:
    ideate: sonnet
    validate: haiku
    scaffold: haiku
    plan: sonnet
    build_simple: haiku     # tests, docs, README, CI, examples, lint fixes
    build_complex: sonnet   # core features, algorithms, refactors, MCP/skill logic
    fix: sonnet
    commit_message: haiku
    release: haiku
  escalate_on_failure: true # haiku task fails gate twice -> retry once with sonnet
  max_turns_per_task: 25
  task_timeout_sec: 900
github:
  owner: YOUR_GH_USER_OR_ORG
  repo_prefix: ""
  visibility: public
  license: MIT
limits:
  max_commits_per_day: 80
  max_sessions_per_day: 90
  max_repos_per_day: 5
  max_cost_usd_per_day: 0        # 0 = disabled (subscription); set for API key use
  max_consecutive_failures: 5
  min_seconds_between_sessions: 20
quality:
  reject_if_existing_repo_stars_over: 200
  min_tasks_per_project: 10
  max_tasks_per_project: 20
  require: [readme, license, tests, ci]
paths:
  workspace: /home/ubuntu/factory-workspace
  stop_file: ./STOP
```

## 5. Data Model (SQLite: `state/factory.db`)

```sql
CREATE TABLE ideas (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  category TEXT NOT NULL,          -- skill | mcp | cli | devtool | data | template | agent-tool
  pitch TEXT NOT NULL,
  source TEXT,                     -- where the idea came from
  score REAL,                      -- usefulness 0-10
  status TEXT NOT NULL,            -- new | rejected | approved | building | shipped | abandoned
  reject_reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  idea_id INTEGER REFERENCES ideas(id),
  repo_name TEXT UNIQUE NOT NULL,
  repo_url TEXT,
  local_path TEXT,
  language TEXT,
  status TEXT NOT NULL,            -- scaffolded | planning | building | releasing | shipped | stalled
  created_at TEXT NOT NULL
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),
  seq INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  acceptance TEXT NOT NULL,        -- concrete, testable criteria
  complexity TEXT NOT NULL,        -- simple | complex (drives model choice)
  model_used TEXT,                 -- haiku | sonnet (last attempt)
  status TEXT NOT NULL,            -- pending | in_progress | done | failed | skipped
  attempts INTEGER DEFAULT 0,
  commit_sha TEXT,
  updated_at TEXT
);

CREATE TABLE sessions (
  id INTEGER PRIMARY KEY,
  project_id INTEGER,
  task_id INTEGER,
  stage TEXT NOT NULL,
  model TEXT,                      -- haiku | sonnet
  started_at TEXT,
  ended_at TEXT,
  exit_code INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd REAL,
  log_path TEXT
);

CREATE TABLE daily_counters (
  day TEXT PRIMARY KEY,            -- YYYY-MM-DD local time
  commits INTEGER DEFAULT 0,
  sessions INTEGER DEFAULT 0,
  repos INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0
);
```

## 6. State Machine

```
IDLE
 -> IDEATE      (if no approved idea in queue)
 -> VALIDATE    (each new idea)
 -> SCAFFOLD    (pick best approved idea)
 -> PLAN        (write tasks into DB + TASKS.md)
 -> BUILD_LOOP  (one task per iteration until all done or failed)
 -> RELEASE     (tag, changelog, publish)
 -> IDLE
```

Guard checks run before every transition and every session: stop file, daily caps, failure streak, allowlist.

## 7. Stage Specs

### 7.1 Ideate (`ideate.py`, `prompts/ideate.md`)
- Run one headless session with WebSearch enabled.
- Sources to mine:
  - GitHub issues labeled "feature request" or "help wanted" with many reactions on popular repos.
  - Awesome-lists with obvious gaps.
  - Claude Code / Codex skill and MCP directories: find missing skills or servers.
  - Hacker News and Reddit "I wish there was a tool for X" threads.
  - `user_pain_points.md` (hand-maintained file with the owner's recurring manual tasks).
- Output: JSON array of 10 ideas, each with `title, category, pitch, source, est_scope_hours`.
- Scope filter: each idea must be buildable in 10-20 small tasks and be useful on its own.
- Insert into `ideas` with status `new`.

### 7.2 Validate (`validate.py`, `prompts/validate.md`)
- For each new idea, run `gh search repos "<keywords>" --limit 10 --json name,stargazersCount,pushedAt,description`.
- Reject if a maintained repo (pushed within 12 months) with stars over `reject_if_existing_repo_stars_over` does the same job.
- Ask the agent to score usefulness 0-10 with a one-line rationale.
- Approve ideas scoring 7 or higher. Store `reject_reason` for the rest.

### 7.3 Scaffold (`scaffold.py`)
- Pick the highest-scored approved idea.
- Create the repo: `gh repo create <owner>/<name> --public --license mit --clone`.
- Copy the matching template (`python`, `node`, `skill`, `mcp`).
- Write initial `README.md` skeleton, `.gitignore`, `CLAUDE.md` (project conventions, test command, lint command), and CI workflow.
- Commit as `chore: scaffold project` and push.
- Insert into `projects`.

### 7.4 Plan (`planner.py`, `prompts/plan.md`)
- One session. Input: idea pitch and category. Output: JSON list of 10-20 tasks.
- Each task must be atomic (one commit), have an `acceptance` field that is testable, and be ordered by dependency.
- Each task must have `complexity`:
  - `simple` (Haiku): tests for existing code, docs, README sections, examples, CI/lint config, changelog, small renames, typing fixes.
  - `complex` (Sonnet): core features, parsing/algorithm logic, API design, MCP/skill logic, refactors touching multiple files.
- Required task mix:
  - Core feature tasks.
  - A test task alongside every feature task.
  - Edge cases and error handling.
  - CLI or API polish.
  - Usage examples.
  - README sections (install, usage, examples, FAQ).
  - CI, lint, type checks.
  - Release prep and changelog.
- Insert into `tasks`. Also write `TASKS.md` in the repo for visibility.

### 7.5 Build Loop (`builder.py`, `prompts/build_task.md`)
For each pending task, in order:
1. Guard check.
2. Mark `in_progress`, `attempts += 1`.
3. Pick model: `simple` -> `stage_models.build_simple` (Haiku), `complex` -> `stage_models.build_complex` (Sonnet). Store in `tasks.model_used`.
4. Fresh headless session with:
   - Task title, description, acceptance criteria.
   - Instruction: implement only this task, add or update tests, do not commit, do not touch unrelated files.
   - Working directory: the project repo.
5. Run the gate (7.6).
6. If the gate passes: `git add -A`, commit with a conventional message generated from the diff (Haiku), push, record `commit_sha`, mark `done`.
7. If the gate fails: run one fix session (`prompts/fix_gate.md`, Sonnet) with the gate output, re-run the gate.
8. If it still fails: `git reset --hard HEAD`, `git clean -fd`. If the task ran on Haiku and `escalate_on_failure` is true, retry once from scratch on Sonnet. Otherwise mark `failed` when `attempts >= 2`, else retry.
9. Increment `daily_counters`.
10. Sleep `min_seconds_between_sessions`.

Agent call shape (verify flags with `claude --help`):
```
claude -p "<prompt>" \
  --output-format json \
  --max-turns 25 \
  --model <haiku|sonnet> \
  --permission-mode acceptEdits \
  --allowedTools "Read,Write,Edit,Bash(git status),Bash(git diff),Bash(pytest*),Bash(npm test*),Bash(ruff*)"
```
- Run inside a sandbox user or container. Use `--dangerously-skip-permissions` only inside an isolated environment with no access to other credentials.
- Parse the JSON result for token usage and cost; write to `sessions`.

Codex alternative: `codex exec "<prompt>"` with the same wrapper interface in `agent.py`.

### 7.6 Gate (`gate.py`)
A commit is allowed only if all pass:
- Diff is non-empty and touches at least one non-whitespace line.
- Diff does not modify only comments or only `.gitignore`.
- Tests pass (`pytest -q` or `npm test`).
- Lint passes (`ruff check` or `eslint`).
- Type check passes if configured (`mypy` or `tsc --noEmit`).
- Secret scan clean (`gitleaks detect --no-git` on the staged diff, or a regex fallback).
- Diff size sanity: under 800 changed lines, no binary blobs over 500 KB.
- Task-specific: the acceptance criteria command (if present) exits 0.

### 7.7 Release (`release.py`, `prompts/release.md`)
- Trigger: all tasks `done` or `skipped`.
- Verify: README has install, usage, and example sections; LICENSE exists; CI is green on the default branch (`gh run list`).
- Agent session writes `CHANGELOG.md`.
- Tag `v0.1.0`, `gh release create`.
- Optional publish (`npm publish` or `twine upload`) behind `config.publish: true` and a token in the environment.
- Set repo description and topics via `gh repo edit`.
- Mark project `shipped`.

### 7.8 Maintenance Pass (optional, after MVP)
- For shipped repos: respond to issues with a triage session, fix small bugs, bump dependencies.
- These are real commits and count toward the daily target.
- Weekly cull: archive repos with zero stars, zero forks, and zero clones after 30 days (manual approval).

## 8. Commit Math

- 65 commits/day equals 4-5 projects at 13-15 tasks each, or fewer, deeper projects.
- Prefer depth over count: 1-2 solid repos with 30+ commits beat 5 shells.
- The scheduler should not start a new project if the current one still has pending tasks, unless its tasks are all blocked.

## 9. Token and Cost Controls

- One task equals one fresh session. Keep `CLAUDE.md` short.
- Pass only relevant file paths in the prompt.
- Use `--max-turns` and a per-task timeout.
- Model routing: Haiku for validate, scaffold, simple build tasks, commit messages, release. Sonnet for ideate, plan, complex build tasks, gate-fix sessions.
- Target split: 40-50% of tasks `simple` (Haiku). The planner prompt must label complexity accurately.
- Track per-model token use in `sessions` (add `model TEXT` column) and report Haiku vs Sonnet share and escalation rate in `report`.
- If the Haiku escalation rate exceeds 30% over a day, log a warning and reclassify borderline task types as `complex`.
- Ideate and validate once per project, not per commit.
- Track `input_tokens`, `output_tokens`, and `cost_usd` per session in `sessions`.
- On subscription auth: expect 5-hour and weekly caps. The runner must detect rate-limit errors, back off until the reset time, and resume.
- On rate limit: write `state/paused_until`, sleep, and continue. Do not retry in a tight loop.

## 10. Operations

- `systemd` service runs `python -m factory.main`, `Restart=on-failure`, `RestartSec=60`.
- Timer or in-process scheduler spreads sessions across the day.
- Logs: `state/logs/<date>/<session_id>.json` plus a rotating `factory.log`.
- CLI helpers:
  - `python -m factory.main status` (today's counters, current project, current task)
  - `python -m factory.main pause` and `resume` (create or remove the stop file)
  - `python -m factory.main report` (table of projects, tasks, commits, cost)

## 11. Build Order for Claude Code (each item is one commit or a small group)

**Phase 1: Foundation**
1. Init repo, `pyproject.toml`, ruff, pytest, CI workflow.
2. `config.yaml` loader with validation and tests.
3. `db.py` with schema creation and migrations, plus tests.
4. `log.py` and `guard.py` (stop file, caps, allowlist), plus tests.
5. `agent.py`: subprocess wrapper for `claude -p`, `--model` selection (haiku/sonnet), JSON parsing, timeout, rate-limit detection, plus tests with a fake binary.
5b. `router.py`: maps stage and task complexity to model, handles Haiku-to-Sonnet escalation, plus tests.

**Phase 2: Pipeline**
6. `gate.py` with each check as a separate function, plus tests.
7. `scaffold.py` and `templates/python`, `templates/node`.
8. `templates/skill` and `templates/mcp`.
9. `ideate.py` and prompt, with JSON schema validation.
10. `validate.py` with `gh search` integration and scoring.
11. `planner.py` and prompt, with task JSON validation.
12. `builder.py`: single-task execution, commit, push, rollback.
13. `builder.py`: fix-session retry logic and failure handling.
14. `release.py`.

**Phase 3: Orchestration**
15. `main.py` state machine with resume-from-DB on restart.
16. CLI subcommands: `status`, `pause`, `resume`, `report`.
17. Rate-limit backoff and `paused_until` handling.
18. `systemd` unit files and install script.

**Phase 4: Hardening**
19. Dry-run mode: full pipeline against a local bare git remote, no GitHub calls.
20. Integration test: fake agent produces known diffs; verify commits, counters, rollback.
21. Secret scan integration and tests with planted fake secrets.
22. Docs: README with setup, config reference, safety notes.

**Phase 5: Extensions (after MVP is stable)**
23. Maintenance pass (issues, dependency bumps).
24. `user_pain_points.md` ingestion.
25. Codex driver.
26. Weekly report generator (Markdown).

## 12. Acceptance Criteria for the Factory

- Dry run completes one full project lifecycle with a fake agent and no network.
- With a real agent: scaffolds a repo, builds at least 10 tasks, and produces one commit per task with passing CI.
- Stop file halts the runner within one task.
- Daily caps are enforced and reset at local midnight.
- Restarting the process resumes from DB state without duplicate commits or repos.
- No empty or whitespace-only commits are ever created (verified by test).
- Planted secrets in a task diff are blocked by the gate.

## 13. Open Decisions

- Subscription auth vs API key (affects cost tracking and rate-limit behavior).
- Public org vs personal account for generated repos.
- Whether to auto-publish to npm/PyPI or GitHub releases only.
- Sandbox: dedicated Linux user vs Docker container.
