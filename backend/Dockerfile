FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/huggingface \
    TROCR_MODEL=microsoft/trocr-base-handwritten

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r /app/requirements.txt

RUN python -c "import os; from transformers import TrOCRProcessor, VisionEncoderDecoderModel; model_id = os.environ.get('TROCR_MODEL', 'microsoft/trocr-base-handwritten'); TrOCRProcessor.from_pretrained(model_id); VisionEncoderDecoderModel.from_pretrained(model_id)"

RUN mkdir -p /app/ui

COPY backend /app/backend
COPY index.html styles.css app.js /app/ui/

WORKDIR /app/backend
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
