FROM python:3.12-slim
# alsa-utils + pulse-плагин: Джарвис пишет и играет звук через arecord/aplay, звук уходит в PulseAudio хоста
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg alsa-utils libasound2-plugins \
    && rm -rf /var/lib/apt/lists/*
COPY docker/asound.conf /etc/asound.conf
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import onnx_asr; onnx_asr.load_model('gigaam-v3-e2e-rnnt')"
RUN python -c "from huggingface_hub import hf_hub_download as d; [d('intfloat/multilingual-e5-small', f) for f in ('onnx/model.onnx', 'onnx/tokenizer.json')]"
COPY . .
ENV DB_PATH=/data/assistant.db
CMD ["sh","-c","mkdir -p $(dirname $DB_PATH) && python -m scripts.import_csv --db $DB_PATH --if-empty && exec uvicorn --factory app.main:create --host 0.0.0.0 --port 8000"]
