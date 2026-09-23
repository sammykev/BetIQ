# BetIQ API. Build from the repository root — the legacy EPL and Champions
# League CSVs live there:
#   docker build -f backend/Dockerfile -t betiq-api .
# Runs on x86 and ARM; used by the server setup (deploy/vm: Google Cloud,
# Oracle Cloud or any Ubuntu machine) and Hugging Face Spaces (deploy/huggingface).
# If Docker Hub rate-limits you, the same image is on Amazon's mirror:
#   --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# XGBoost needs OpenMP; curl for the health check
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# main.py reads these as ../epl-final.csv and ../champions-league-*.csv
COPY epl-final.csv champions-league-*.csv /app/
COPY backend/ .

# data/ is a persistent volume at runtime. The copy shipped with the code
# seeds it (see docker-entrypoint.sh).
RUN cp -a data /app/seed-data && chmod +x docker-entrypoint.sh

# Run as an unprivileged user — Hugging Face Spaces require uid 1000, and
# the app writes its model and caches under /app/backend/data
RUN useradd -m -u 1000 betiq && chown -R betiq /app
USER betiq

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
# One worker: predictions, caches and the scheduler live in this process
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
