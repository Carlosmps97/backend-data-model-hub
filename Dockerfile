# ── Stage 1: build / install ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System dependencies needed only at build time
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runner

WORKDIR /app

# Non-root user for least-privilege principle
RUN useradd -m -u 1001 appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12  /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin             /usr/local/bin

# Copy application source (excluding test data, local config, venv)
COPY api/ ./api/
COPY src/ ./src/

RUN chown -R appuser:appuser /app

USER appuser

# Azure App Service will use WEBSITES_PORT to forward traffic.
# Uvicorn binds to 0.0.0.0:8000 by default.
EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
