FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch first (much smaller than default CUDA version)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Heavy ML deps that rarely change
RUN pip install --no-cache-dir \
    sentence-transformers==3.3.1 \
    scipy \
    scikit-learn \
    numpy

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
