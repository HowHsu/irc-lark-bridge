FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 公开仓库用 HTTPS；私有仓库需配合 BuildKit SSH 或 token
RUN git clone https://github.com/HowHsu/irc-lark-bridge.git . \
    && pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]
