FROM python:3.12-slim

# poppler: PDF text layers (pdftotext/pdffonts) used by layer 1 and the parser cross-check
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/evidence
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest ./ingest
COPY reason ./reason
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts
COPY .streamlit ./.streamlit
COPY showcase ./showcase

RUN useradd --create-home evidence && mkdir -p /data && chown evidence /data
USER evidence

ENV EVIDENCE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/evidence

EXPOSE 8501
# PORT is set by hosts like Render; 8501 otherwise
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/_stcore/health' % os.environ.get('PORT', '8501'))"
CMD ["sh", "-c", "exec streamlit run app/streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}"]
