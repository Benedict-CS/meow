# Cat Gallery — slim Python + ffmpeg image.
# pillow-heif's wheel bundles libheif, so we only need ffmpeg from apt.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install Pillow==12.* pillow-heif==1.*

COPY server.py /app/
COPY static/ /app/static/

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data

RUN mkdir -p /data/uploads /data/thumbs
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/list',timeout=2).status==200 else 1)"

CMD ["python", "server.py"]
