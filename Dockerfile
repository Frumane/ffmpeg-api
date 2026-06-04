FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg fonts-dejavu-core fontconfig && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:5000 --workers 1 --timeout 600 --access-logfile - --error-logfile - main:app
