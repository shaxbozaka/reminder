FROM python:3.12-slim

WORKDIR /app

# System deps for audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir faster-whisper

# Copy app code and data
COPY src/ src/
COPY data/ data/
COPY alembic/ alembic/
COPY alembic.ini .

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8443 8090

CMD ["python", "-m", "src.main"]
