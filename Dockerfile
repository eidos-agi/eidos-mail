FROM ghcr.io/eidos-agi/eidos-mail-base:latest

WORKDIR /app

# Light app deps only (no torch/sentence-transformers — already in base)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
