# A imagem é grande (~4GB): o Docling traz torch, e os modelos de layout/OCR
# ficam embutidos. O primeiro build demora; os seguintes reusam o cache.
#
# Os modelos são baixados no build, e não em runtime, de propósito: o container
# sobe pronto para converter, sem depender de rede na hora do request. Isso é o
# que torna a demo reproduzível.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models

WORKDIR /app

# libgl e libglib são exigidas pelas dependências de visão do Docling.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch de CPU: o serviço faz layout e parse, não inferência pesada, e a
# variante CUDA acrescentaria vários GB sem ganho aqui.
# torchvision é obrigatório: sem ele o `AutoImageProcessor` do transformers
# não carrega e o modelo de layout falha já no primeiro /convert.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision \
 && pip install -r requirements.txt

# layout + tableformer cobrem o caminho padrão (PDF com camada de texto).
# rapidocr cobre o caminho opcional `ocr=true`, para editais digitalizados —
# é bem mais leve que o easyocr e não precisa de GPU.
RUN docling-tools models download -o /opt/docling-models layout tableformer rapidocr

COPY app.py .

EXPOSE 5001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5001"]
