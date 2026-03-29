# Python 3.11 slim — minimal base image, no bloat
FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Install dependencies first (cached if requirements.txt hasn't changed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite (will be overridden by volume mount)
RUN mkdir -p /data

# Environment: tell the app where the DB lives (Railway volume path)
ENV DB_PATH=/data/scanner.db
ENV REPORT_DIR=/data/reports
ENV PYTHONUNBUFFERED=1

# Expose FastAPI port
EXPOSE 8000

# Run the FastAPI server (handles both health checks and on-demand triggers)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
