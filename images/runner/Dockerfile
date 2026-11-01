# Test-runner image (plan 1.1; generate design D2): the patched SUT backend image + one layer.
# BASE is the env stage's backend image for the current patch set; built by `tp generate image`.
ARG BASE
FROM ${BASE}
COPY platform_rt-*.whl /tmp/
RUN uv pip install --python /app/.venv/bin/python --no-cache /tmp/platform_rt-*.whl mutmut==3.8.1 \
    && rm /tmp/platform_rt-*.whl
WORKDIR /app/backend
