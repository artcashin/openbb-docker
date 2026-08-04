FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /opt/key-maint
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install .
CMD ["python", "-m", "app.main"]
