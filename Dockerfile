FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_SERVER_DIR=/app/server \
    MCP_PACKAGES_DIR=/app/tool_packages

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --uid 1000 --home-dir /app --no-create-home app \
    && mkdir /app/server /app/tool_packages \
    && chown -R app:app /app

COPY --chown=app:app main.py .

USER app

EXPOSE 8555

CMD ["python", "main.py"]