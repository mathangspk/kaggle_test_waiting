FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
ENV MONITOR_DB=/data/monitor.sqlite3 ACCOUNTS_ENV=/run/secrets/accounts.env PYTHONUNBUFFERED=1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000", "--workers", "1"]
