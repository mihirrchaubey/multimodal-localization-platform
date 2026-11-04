FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir ".[gemini]" && useradd -m appuser && mkdir /data && chown appuser /data
USER appuser
ENV LOC_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "--factory", "app.api.main:app_factory", "--host", "0.0.0.0", "--port", "8000"]
