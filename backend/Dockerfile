FROM python:3.11-slim AS ta_lib_builder

ARG TA_LIB_VERSION=0.4.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

RUN curl -fsSL -o ta-lib.tar.gz "https://github.com/ta-lib/ta-lib/releases/download/v${TA_LIB_VERSION}/ta-lib-${TA_LIB_VERSION}-src.tar.gz" \
    && mkdir -p ta-lib-src \
    && tar -xzf ta-lib.tar.gz -C ta-lib-src --strip-components=1 \
    && cd ta-lib-src \
    && ./configure --prefix=/usr \
    && make \
    && make install

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ta_lib_builder /usr/lib/libta_lib.so* /usr/lib/
COPY --from=ta_lib_builder /usr/include/ta-lib /usr/include/ta-lib

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY requirements-dev.txt /app/requirements-dev.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir -r /app/requirements-dev.txt

COPY backend /app/backend
COPY tests /app/tests
COPY pytest.ini /app/pytest.ini

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app

USER appuser

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
