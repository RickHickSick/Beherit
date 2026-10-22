FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir .

# Copy application code
COPY src/ /app/src/

# Default data directory
RUN mkdir -p /app/data/agents

EXPOSE 8650

CMD ["uvicorn", "beherit.main:app", "--host", "0.0.0.0", "--port", "8650"]
