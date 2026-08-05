# cache-chart: the kdb+ cache demo — Workspace widget + standalone scroll page.
# Loopback-only; Tailscale Serve publishes it (see repo compose).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# plotly.js is vendored at build time: the page must render on a tailnet with
# no route to a CDN.
ADD https://cdn.plot.ly/plotly-2.35.2.min.js /srv/app/static/plotly.min.js

COPY pyproject.toml ./
COPY app/ app/
COPY widgets.json ./
RUN pip install . && chmod 644 app/static/plotly.min.js
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "6906"]
