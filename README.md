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

## License

MIT — see [LICENSE](LICENSE).
