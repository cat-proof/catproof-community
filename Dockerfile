FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 catproof
COPY *.py alembic.ini LOCAL.md ./
COPY adapters ./adapters
COPY migrations ./migrations
COPY account_static ./account_static
COPY brand_static ./brand_static
COPY demo_static ./demo_static
COPY federation_static ./federation_static
COPY product_static ./product_static
COPY static ./static
COPY guides ./guides
COPY local ./local
COPY scripts ./scripts
COPY tests ./tests
LABEL org.opencontainers.image.title="CatProof Community Edition" \
      org.opencontainers.image.source="https://github.com/cat-proof/catproof-community"
USER catproof
EXPOSE 10000
CMD ["python", "start.py"]
