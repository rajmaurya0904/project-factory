# project-factory

Autonomous runner that finds small, useful open-source project ideas, validates
them against what already exists on GitHub, builds them one small commit at a
time through Claude Code, and ships them with tests, CI, and a README.

Status: **MVP**. Every stage in [plan.md](plan.md) section 7 is implemented
and tested (ideate, validate, scaffold, plan, build loop + gate, release),
wired together by `factory/main.py`'s state-machine loop. See plan.md for the
full design; Phase 5 (issue triage, dependency bumps, weekly reports) is
deliberately not built yet.

## Setup

```bash
pip install -e ".[dev]"
```

Copy `config.yaml` and adjust it for your setup -- see **Configuration**
below for what each key does. You'll also need `gh` (authenticated) and, for
the secret-scan gate, `gitleaks` on `PATH`.

## Development

```bash
ruff check .
pytest -q
```

Tests never touch the network: every `claude`/`gh`/`gitleaks` call is
injectable, and tests pass fake binaries or a local bare git remote instead
(see `tests/fixtures/`). `tests/test_main.py` drives the same `run_once`/
`run_forever` code path the real runner uses, end to end, with a fake agent
and a local git remote -- that combination *is* this project's dry-run mode;
there's no separate flag for it.

## Running it

```bash
python -m factory.main run       # the persistent loop (what systemd runs)
python -m factory.main status    # today's counters, current project/task
python -m factory.main pause     # touch the stop file; the loop exits after its current task
python -m factory.main resume    # remove the stop file
python -m factory.main report    # per-project table: status, tasks done, commits
```

All four subcommands accept `--config`, `--db`, and `--state-dir` if you're
not running from the repo root with the default layout (`config.yaml`,
`state/factory.db`, `state/`).

## Configuration (`config.yaml`)

- **agent** -- which CLI drives sessions (`driver: claude|codex`), the real
  model name behind each alias (`models.haiku`/`models.sonnet`), which alias
  each pipeline stage and build complexity uses (`stage_models`), whether a
  Haiku task that fails the gate twice gets one Sonnet retry
  (`escalate_on_failure`), and per-session limits (`max_turns_per_task`,
  `task_timeout_sec`).
- **github** -- `owner` (account/org repos are created under), `repo_prefix`
  (defaults to `factory-`; also the allowlist prefix guard.py enforces),
  `visibility`, `license`.
- **limits** -- daily caps (`max_commits_per_day`, `max_sessions_per_day`,
  `max_repos_per_day`, `max_cost_usd_per_day` -- `0` disables the cost cap,
  intended for subscription auth), `max_consecutive_failures` before the
  runner halts itself, and `min_seconds_between_sessions` to spread load.
- **quality** -- `reject_if_existing_repo_stars_over` (validate.py's
  competitor-blocking threshold), `min_tasks_per_project`/
  `max_tasks_per_project` (planner.py's task-count range), `require` (repo
  requirements the release gate checks for).
- **paths** -- `workspace` (where repos get cloned/scaffolded) and
  `stop_file` (touch it to pause; `pause`/`resume` do this for you).

## Safety

This runner is designed to run as a dedicated, unprivileged system user with
its own GitHub token and its own Claude Code login — never the same account
used for anything else. See `plan.md` sections 2 and 7.5 for the hard rules
(no force-push, no empty commits, allowlisted repos only, daily caps, secret
scanning before every push).

## Deployment

Run this as a dedicated, unprivileged system user (e.g. `factory`, home
`/home/factory`) — never your own account. That user gets its own GitHub
token and its own Claude Code login, checked out at
`/home/factory/project-factory` with a venv at
`/home/factory/project-factory/.venv`.

Install the systemd service:

```bash
sudo bash systemd/install.sh
```

This resolves the `claude` CLI's path (installed per-user via nvm) into
`systemd/factory.env` (git-ignored, regenerated on re-run) and installs
`systemd/factory.service`. It does not start anything — review it, then:

```bash
sudo systemctl enable --now factory.service
sudo systemctl status factory.service
journalctl -u factory.service -f
```

There is no `factory.timer`: `factory/main.py` runs a persistent loop, so one
long-running service with `Restart=on-failure` fits better than periodic
wake-ups.

To pause the runner, run `python -m factory.main pause` (or touch the stop
file directly -- `config.yaml`'s `paths.stop_file`, default `./STOP` in the
working directory). The runner finishes its current task and exits cleanly.
`Restart=on-failure` does not restart on a clean exit, so the service stays
stopped; run `python -m factory.main resume` and `systemctl start
factory.service` to pick back up.

If a rate limit is hit mid-session (subscription 5-hour/weekly caps), the
runner writes `state/paused_until` and sleeps until it elapses on its own --
no manual intervention needed.

## License

MIT — see [LICENSE](LICENSE).
