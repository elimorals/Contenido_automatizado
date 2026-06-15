# Contributing

## Setup local

```bash
git clone <repo>
cd contenido
cp .env.example .env       # editar con tus keys
cp config.example.toml config.toml
make install
make test-fast
```

## Convenciones

- **Lint**: ruff (`make lint`)
- **Format**: ruff format (`make format`)
- **Types**: mypy strict (`make typecheck`)
- **Tests**: pytest, asyncio_mode=auto. Marcar slow/integration con `@pytest.mark.slow`.

## Estructura de commits

```
<scope>: <imperative summary>

[optional body]
```

Scopes:
- `narrative` — reasoners
- `planning` — beats/cards/safe_zone
- `tts` — engines y timing
- `visual` — stock/IA/selector
- `editor` — ffmpeg
- `subtitles` — word-burst/SRT
- `api` — FastAPI
- `webui` — Streamlit
- `cli` — Typer
- `config` — shared/config
- `schemas` — shared/schemas
- `docs` — documentación
- `infra` — Docker/CI/deps

## PRs

- Squash merge para histórico limpio
- Pasar `make lint test typecheck` antes de pedir review
- Update `PLAN.md` si cambias scope de una fase
- Update `docs/DECISIONS.md` si tomas decisión arquitectónica nueva
