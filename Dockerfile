# AnimaBot — see README「用 Docker 运行」and docker-compose.yml
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
# PIP_INDEX_URL can be set at build time for a nearer mirror, e.g.
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=
RUN pip install ${PIP_INDEX_URL:+-i $PIP_INDEX_URL} -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 anima \
    && mkdir -p data \
    && chown -R anima:anima data
USER anima

# Inside Docker the bot must listen on all interfaces so NapCat (another container) can connect.
ENV HOST=0.0.0.0 \
    PORT=8080 \
    BOT_ADMIN_ALLOW_PRIVATE=true
EXPOSE 8080
CMD ["python", "bot.py"]
