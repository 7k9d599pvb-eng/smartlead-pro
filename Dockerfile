FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd --create-home --uid 10001 botuser
USER botuser
CMD ["python", "-m", "smartlead"]

FROM base AS test
USER root
COPY requirements-dev.lock ./
RUN pip install --no-cache-dir -r requirements-dev.lock
COPY tests ./tests
USER botuser
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
