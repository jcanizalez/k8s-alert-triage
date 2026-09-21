FROM python:3.13-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" \
      fastapi==0.141.1 \
      uvicorn==0.52.4 \
      httpx==0.28.1 \
      kubernetes==36.0.3

COPY eval/baseline.py serve/triage.py ./

EXPOSE 8080
CMD ["uvicorn", "triage:app", "--host", "0.0.0.0", "--port", "8080"]
