# Verdict, the Agentic SLA Auditor: API service (the dashboard uses the same image with a different command, see docker-compose.yml)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt requirements-full.txt .
RUN pip install --no-cache-dir -r requirements-full.txt

COPY . .

# Run as an unprivileged user; the ledger lives on a mounted volume.
RUN useradd --create-home sla && mkdir -p /app/ledger && chown -R sla /app/ledger
USER sla

EXPOSE 8000
CMD ["uvicorn", "service.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
