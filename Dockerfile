FROM python:3.9-alpine

WORKDIR /app

RUN apk add --no-cache \
    gcc \
    musl-dev \
    linux-headers \
    libffi-dev \
    jpeg-dev \
    zlib-dev

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN adduser -D -s /bin/sh amapuser
USER amapuser

EXPOSE 8080

CMD ["python", "app.py"]