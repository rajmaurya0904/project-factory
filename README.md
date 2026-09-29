# project-factory

Autonomous runner that finds small, useful open-source project ideas, validates
them against what already exists on GitHub, builds them one small commit at a
time through Claude Code, and ships them with tests, CI, and a README.

Status: **early build**. See [plan.md](plan.md) for the full design and build
order. This repo currently has the foundation pieces (config, DB, guards,
logging); the pipeline stages (ideate/validate/scaffold/plan/build/gate/release)
land next.

## Setup

```bash
pip install -e ".[dev]"
```

Copy `config.yaml` and adjust `github.owner`, limits, and paths for your setup.

## Development

```bash
ruff check .
pytest -q
```

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

To pause the runner, touch the stop file (`config.yaml`'s `paths.stop_file`,
default `./STOP` in the working directory) — the runner finishes its current
task and exits cleanly. `Restart=on-failure` does not restart on a clean
exit, so the service stays stopped; remove `STOP` and run
`systemctl start factory.service` to resume. `python -m factory.main pause` /
`resume` are meant to manage the same stop file, but that CLI subcommand is
currently a stub (see plan.md section 10) — not implemented yet.

## License

MIT — see [LICENSE](LICENSE).
