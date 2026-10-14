# Headless Chrome reading SofaScore's API for the API server (server.py).
# Built and run by deploy/vm/docker-compose.yml as the "browser" service.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install playwright==1.56.0 \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py .
RUN useradd -m -u 1000 browser
USER browser

EXPOSE 8100
CMD ["python", "server.py"]
