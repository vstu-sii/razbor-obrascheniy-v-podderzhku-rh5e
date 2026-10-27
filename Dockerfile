FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY ml ./ml
COPY migrations ./migrations
COPY data ./data
RUN useradd --uid 10001 --create-home appuser && mkdir /data && chown appuser /data
USER appuser
ENV DATABASE_PATH=/data/triage.db TRACE_PATH=/data/traces.jsonl BASELINE_MODE=extractive APP_FACTORY=backend.main:create_app
ARG APP_REVISION=local
ENV APP_REVISION=$APP_REVISION
EXPOSE 8000
CMD ["python", "backend/bootstrap.py"]
