# Black-box test runner (blackbox design, build item 2): no target code inside. Tests reach the
# target only over HTTP, on the run's internal network. Built by `tp generate image` when the
# target has no image of its own.
FROM python:3.14-slim
COPY platform_rt-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/platform_rt-*.whl schemathesis==4.33.1 && rm /tmp/platform_rt-*.whl
WORKDIR /work
