# Browser test runner (P7, docs/status/browser/001 D12): the black-box runner plus Playwright and
# its pinned Chromium (headless shell), for `.ui.yaml` flows. BASE is the black-box runner's tag;
# built by `tp generate` (tp/stages/generate/ui.py) when a target has UI flows.
ARG BASE
FROM ${BASE}
RUN pip install --no-cache-dir playwright==1.64.1 pyyaml==6.0.3 \
    && python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
