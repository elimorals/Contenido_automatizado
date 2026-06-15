.PHONY: help install dev test lint format clean docker-up docker-down docker-build docker-logs

help:
	@echo "contenido — comandos disponibles:"
	@echo ""
	@echo "  make install        Instalar dependencias con uv"
	@echo "  make dev            Modo desarrollo (API + WebUI en local, sin Docker)"
	@echo "  make test           Ejecutar suite completa de tests"
	@echo "  make test-fast      Solo tests unitarios (sin slow / integration)"
	@echo "  make lint           Lint con ruff"
	@echo "  make format         Formatear con ruff"
	@echo "  make typecheck      Type check con mypy"
	@echo "  make clean          Limpiar archivos generados"
	@echo ""
	@echo "  make docker-up      Levantar stack completo (API + WebUI + Redis + control-plane)"
	@echo "  make docker-down    Detener stack"
	@echo "  make docker-build   Reconstruir imágenes"
	@echo "  make docker-logs    Tail de logs"

install:
	uv sync --extra dev

dev:
	@echo "Iniciando API en :8000 y WebUI en :8501..."
	uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000 &
	streamlit run apps/webui/Main.py --server.port 8501

test:
	uv run pytest

test-fast:
	uv run pytest -m "not slow and not integration"

lint:
	uv run ruff check apps core orchestration shared tests

format:
	uv run ruff format apps core orchestration shared tests
	uv run ruff check --fix apps core orchestration shared tests

typecheck:
	uv run mypy apps core orchestration shared

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-logs:
	docker compose logs -f
