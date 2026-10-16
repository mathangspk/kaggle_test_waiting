FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py discovery_contract.py ./
COPY --from=frontend /frontend/dist ./static
COPY roadmap.json ./roadmap.json
ENV MONITOR_DB=/data/monitor.sqlite3 ACCOUNTS_ENV=/run/secrets/accounts.env PYTHONUNBUFFERED=1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000", "--workers", "1"]
