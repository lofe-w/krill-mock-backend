FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/ ./config/

EXPOSE 8000
# registry.py 以 /app 为根、读 /app/config（与 COPY 一致）
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
