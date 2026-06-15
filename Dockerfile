# =============================================================================
# Dockerfile — contenido
# Multi-stage: build deps + ffmpeg + Montserrat font
# =============================================================================

FROM python:3.11-slim AS base

# Sistema: ffmpeg (hardreq), fonts, curl para healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Descargar Montserrat Bold (font default para word-burst)
RUN mkdir -p /usr/share/fonts/truetype/montserrat && \
    curl -L -o /tmp/montserrat.zip "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf" && \
    cp /tmp/montserrat.zip /usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf && \
    fc-cache -f && \
    rm /tmp/montserrat.zip

# uv para gestión de dependencias
RUN pip install --no-cache-dir uv

WORKDIR /app

# Capa de dependencias (cacheable)
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Código de aplicación
COPY apps/ ./apps/
COPY core/ ./core/
COPY orchestration/ ./orchestration/
COPY shared/ ./shared/
COPY resource/ ./resource/

# Storage runtime (volume)
RUN mkdir -p /app/storage /app/output

EXPOSE 8000 8501

# Healthcheck base
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default: API (sobrescribible por compose)
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
