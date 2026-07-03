# Slim single-container image for the hosted decision-memory service.
# Only schema/ + service/ are needed at runtime — not the 5-container dev stack.
FROM python:3.12-slim

WORKDIR /app

COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt

COPY schema/ ./schema/
COPY service/ ./service/

# Railway/Render/Fly inject $PORT; default to 7200 for local runs.
ENV PORT=7200
EXPOSE 7200

CMD ["sh", "-c", "uvicorn service.app:app --host 0.0.0.0 --port ${PORT:-7200}"]
