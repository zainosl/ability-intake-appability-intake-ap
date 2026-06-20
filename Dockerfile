FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV PORT=8787
ENV HOST=0.0.0.0

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_intake_app ./ai_intake_app

EXPOSE 8787

CMD ["python", "ai_intake_app/app.py"]
