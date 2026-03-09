FROM python:3.11-slim

WORKDIR /app

# System deps for pdfplumber / Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpoppler-cpp-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies + gunicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application source
COPY app.py parser.py component_registry.py scrapers.py schema_normalized.sql ./
COPY static/ static/
COPY templates/ templates/

# Persistent data directories (mounted at runtime)
RUN mkdir -p uploads

EXPOSE 5001

# Run with gunicorn: 2 workers, 120s timeout for PDF parsing
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5001", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
