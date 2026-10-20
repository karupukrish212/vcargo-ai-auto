FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/app
COPY pyproject.toml requirements.txt ./
COPY app ./app
RUN pip install --no-cache-dir . && useradd --create-home agent
COPY scripts ./scripts
COPY metadata ./metadata
RUN mkdir -p metadata/generated && chown -R agent:agent metadata
USER agent
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
