from flask import Flask, send_file, Response
import requests
from io import BytesIO
import math
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class CoordinateConverter:
    """坐标转换类"""
    
    def __init__(self):
        self.a = 6378245.0  # 长半轴
        self.ee = 0.00669342162296594323  # 扁率

    def wgs84_to_gcj02(self, lng, lat):
        """WGS84转GCJ02坐标系（火星坐标系）"""
        if self.out_of_china(lng, lat):
            return lng, lat
            
        dlat = self._transform_lat(lng - 105.0, lat - 35.0)
        dlng = self._transform_lng(lng - 105.0, lat - 35.0)
        
        radlat = lat / 180.0 * math.pi
        magic = math.sin(radlat)
        magic = 1 - self.ee * magic * magic
        sqrtmagic = math.sqrt(magic)
        
        dlat = (dlat * 180.0) / ((self.a * (1 - self.ee)) / (magic * sqrtmagic) * math.pi)
        dlng = (dlng * 180.0) / (self.a / sqrtmagic * math.cos(radlat) * math.pi)
        
        mglat = lat + dlat
        mglng = lng + dlng
        
        return mglng, mglat

    def _transform_lat(self, lng, lat):
        ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
        ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
        return ret

    def _transform_lng(self, lng, lat):
        ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
        ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
        return ret

    def out_of_china(self, lng, lat):
        """判断是否在国内"""
        if lng < 72.004 or lng > 137.8347:
            return True
        if lat < 0.8293 or lat > 55.8271:
            return True
        return False

# 初始化坐标转换器
converter = CoordinateConverter()

def tile_to_lnglat(x, y, z):
    """瓦片坐标转经纬度（WGS84）"""
    n = math.pow(2, z)
    lng_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = lat_rad * 180.0 / math.pi
    return lng_deg, lat_deg

def lnglat_to_tile(lng, lat, z):
    """经纬度转瓦片坐标"""
    n = math.pow(2, z)
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = lat * math.pi / 180.0
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

@app.route('/')
def index():
    return "高德地图瓦片代理服务运行中 🗺️"

@app.route('/health')
def health_check():
    """健康检查端点"""
    return "OK"

@app.route('/amap/<int:z>/<int:x>/<int:y>.jpg')
def get_amap_tile(z, x, y):
    """获取高德地图瓦片（带纠偏）"""
    try:
        logger.info(f"请求瓦片: z={z}, x={x}, y={y}")
        
        # 将瓦片坐标转换为WGS84经纬度
        wgs84_lng, wgs84_lat = tile_to_lnglat(x, y, z)
        
        # 转换为GCJ02坐标系（火星坐标系）
        gcj02_lng, gcj02_lat = converter.wgs84_to_gcj02(wgs84_lng, wgs84_lat)
        
        # 将GCJ02坐标转换回瓦片坐标
        gcj02_x, gcj02_y = lnglat_to_tile(gcj02_lng, gcj02_lat, z)
        
        # 构建高德地图URL
        server = (gcj02_x + gcj02_y) % 4
        amap_url = f"https://webrd0{server}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={gcj02_x}&y={gcj02_y}&z={z}"
        
        logger.info(f"高德地图URL: {amap_url}")
        
        # 获取瓦片数据
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(amap_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return send_file(
                BytesIO(response.content), 
                mimetype='image/jpeg',
                as_attachment=False
            )
        else:
            logger.error(f"高德地图请求失败: {response.status_code}")
            return Response(f"Tile not found: {response.status_code}", status=404)
            
    except Exception as e:
        logger.error(f"处理瓦片请求时出错: {e}")
        return Response(f"Error: {str(e)}", status=500)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)