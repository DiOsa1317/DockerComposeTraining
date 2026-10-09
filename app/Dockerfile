FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --no-compile -r requirements.txt \
 && rm -rf /venv/lib/python3.12/site-packages/pip* \
           /venv/lib/python3.12/site-packages/setuptools* \
           /venv/bin/pip*

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY main.py .
USER nobody
CMD ["gunicorn", "-b", "0.0.0.0:5000", "main:app"]
