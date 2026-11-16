FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --no-create-home bot \
    && mkdir /data && chown bot:bot /data
RUN pip install --no-cache-dir tzdata
COPY services/telegram_bot /app/services/telegram_bot
USER 10001:10001
ENTRYPOINT ["python", "-m", "services.telegram_bot"]
CMD ["run"]
