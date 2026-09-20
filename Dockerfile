# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
FROM ${PYTHON_IMAGE} AS wheel
WORKDIR /source
COPY requirements-build.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --no-build-isolation --no-deps --wheel-dir /wheels .

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=0.2.1
ARG REVISION=development
LABEL org.opencontainers.image.title="HA Growatt" \
      org.opencontainers.image.description="Growatt telemetry and local device services" \
      org.opencontainers.image.source="https://github.com/Herbertmt978/HA-Growatt" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      io.hass.type="app" \
      io.hass.version="${VERSION}" \
      io.hass.arch="aarch64|amd64"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /tmp/requirements.lock
COPY --from=wheel /wheels /tmp/wheels
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && python -m pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels /tmp/requirements.lock \
    && groupadd --gid 10001 ha-growatt \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent ha-growatt \
    && mkdir /app/config
USER 10001:10001
EXPOSE 5279/tcp 5781/tcp 5782/tcp
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "-m", "ha_growatt", "healthcheck"]
ENTRYPOINT ["python", "-m", "ha_growatt"]
CMD ["container"]
