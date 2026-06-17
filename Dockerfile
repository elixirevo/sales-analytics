FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////app/data/sales_analytics.db \
    OUTPUT_DIR=/app/reports \
    LOCAL_DRIVE_DIR=/app/uploads \
    TOSS_CLIENT_MODE=mock \
    UPLOAD_MODE=local

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY main.py ./

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data /app/reports /app/uploads

VOLUME ["/app/data", "/app/reports", "/app/uploads"]

ENTRYPOINT ["sales-analytics"]
CMD ["serve"]
