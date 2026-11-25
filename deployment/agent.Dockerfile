FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OTEL_SDK_DISABLED=true
WORKDIR /srv
RUN pip install --no-cache-dir strands-agents==1.66.0 fastapi==0.142.4 uvicorn==0.54.0 httpx==0.28.1 \
    && useradd --uid 10001 --create-home app
COPY --chown=10001:10001 app/__init__.py app/industry_scenarios.py /srv/app/
COPY --chown=10001:10001 agent_runtime /srv/agent_runtime
USER 10001
EXPOSE 8080
CMD ["uvicorn", "agent_runtime.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
