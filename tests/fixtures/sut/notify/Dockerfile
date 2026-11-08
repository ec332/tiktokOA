# notify: a SYNTHETIC known-answer target for iteration 3's new checks (W0 C10, DR-8). No compose
# file on purpose: it is the Dockerfile-reach case. Standard library only, so after one pull of
# the base image it builds offline.
FROM python:3.14-slim
WORKDIR /app
COPY app.py .
EXPOSE 8080
CMD ["python", "app.py"]
