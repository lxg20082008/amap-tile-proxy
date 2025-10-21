# 高德地图瓦片代理服务

支持多架构（x86/ARM）的高德地图瓦片代理服务，解决地图偏移问题。

## 快速开始

```bash
docker run -d -p 8280:8080 ghcr.io/your-username/amap-tile-proxy:main
```

## API 使用

```bash
# 健康检查
curl http://localhost:8280/health

# 获取瓦片
curl http://localhost:8280/amap/10/500/300.jpg
```