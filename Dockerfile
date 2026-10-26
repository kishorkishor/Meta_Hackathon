# What Changed Here: one container serving the page and the API. Works on Hugging Face Spaces (port 7860).
FROM python:3.12-slim

# rasterio's bundled GDAL needs libexpat, which the slim image leaves out
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user app app
COPY --chown=user frontend frontend
COPY --chown=user scripts scripts
# cached demo composites, so the four examples open instantly
COPY --chown=user demo_cache cache/composites

USER user
ENV WCH_CACHE=/app/cache PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "7860"]
