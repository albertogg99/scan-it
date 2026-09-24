FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml server.py ./
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["python", "server.py"]