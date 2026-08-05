# live-grid: OpenBB Workspace live_grid backend streaming EODHD websocket
# prices. Loopback-only; Tailscale Serve publishes it (see repo compose).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# plotly.js is vendored: the page must render on a network with no CDN route.
ADD https://cdn.plot.ly/plotly-2.35.2.min.js /srv/app/static/plotly.min.js

COPY pyproject.toml ./
COPY app/ app/
COPY widgets.json ./
RUN pip install . && chmod 644 app/static/plotly.min.js
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "6903"]
