FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 DB_PATH=/data/app.db
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
WORKDIR /srv
COPY app ./app
COPY dashboard ./dashboard
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8000'),timeout=3)"
CMD ["python", "app/server.py"]
