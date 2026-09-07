FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY .env.example ./.env.example

ENV PYTHONUNBUFFERED=1
ENV SIGNAL_DATA_DIR=/app/data

EXPOSE 8001

CMD ["uvicorn", "backend.agent:app", "--host", "0.0.0.0", "--port", "8001"]
