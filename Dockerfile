# AIchain sidecar — self-host image
#   docker build -t aichaind .
#   docker run -p 8080:8080 -e OPENROUTER_KEY=sk-or-... -v aichain-data:/data aichaind
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY aichaind ./aichaind
COPY tools ./tools
COPY config ./config
RUN pip install --no-cache-dir -e .
ENV AICHAIND_CONFIG_OVERRIDE=/data/config.override.json
VOLUME ["/data"]
EXPOSE 8080
# data_dir preusmeren na volume kroz override koji se kreira pri startu ako ne postoji
CMD ["sh", "-c", "mkdir -p /data && [ -f /data/config.override.json ] || echo '{\"data_dir\": \"/data\"}' > /data/config.override.json; PYTHONPATH=. python -m aichaind.main"]
