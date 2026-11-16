FROM python:3.13-slim
RUN groupadd --gid 10001 vector && useradd --uid 10001 --gid vector --no-create-home vector \
    && mkdir /data && chown vector:vector /data
WORKDIR /app
COPY bridge.py erp.py ./
USER vector
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "bridge.py"]
