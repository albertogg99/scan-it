FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml server.py ./
RUN pip install --no-cache-dir . && useradd --system --no-create-home mcp
USER mcp
EXPOSE 8000
CMD ["python", "server.py"]
