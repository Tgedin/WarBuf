FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cached unless requirements change).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code.
COPY . .

# Ensure the SQLite DB and cache directories exist inside the container.
RUN mkdir -p /app/data /app/.cache

# Point the DB path env var to the persistent volume mount.
ENV PORTFOLIO_DB_PATH=/app/data/portfolio.db

CMD ["python", "main.py"]
