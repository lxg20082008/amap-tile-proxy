FROM python:3.9-alpine

WORKDIR /app

# 安装系统依赖
RUN apk update && apk add --no-cache \
    curl \
    gcc \
    musl-dev \
    jpeg-dev \
    zlib-dev

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 下载GeoIP数据库（可选）
RUN wget -O /tmp/GeoLite2-City.tar.gz "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb" || true
# 验证安装
RUN python -c "import flask; import requests; from PIL import Image; print('✅ 所有依赖安装成功')"

# 复制应用代码
COPY app.py .

EXPOSE 8280

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "app.py"]