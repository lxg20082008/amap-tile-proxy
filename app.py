from flask import Flask, send_file, Response, request, jsonify
import requests
from io import BytesIO
import math
import logging
import json
import geoip2.database
import os
from datetime import datetime, timedelta

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== 坐标转换函数 =====
def out_of_china(lng, lat):
    return not (73.66 <= lng <= 135.05 and 3.86 <= lat <= 53.55)

def transform_lat(lng, lat):
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret

def transform_lng(lng, lat):
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret

def wgs84_to_gcj02(lng, lat):
    if out_of_china(lng, lat):
        return lng, lat
    dlat = transform_lat(lng - 105.0, lat - 35.0)
    dlng = transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - 0.00669342162296594323 * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((6378245.0 * (1 - 0.00669342162296594323)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (6378245.0 / sqrtmagic * math.cos(radlat) * math.pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return mglng, mglat

def tile_to_lnglat(x, y, z):
    n = 2.0 ** z
    lng = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = lat_rad * 180.0 / math.pi
    return lng, lat

def lnglat_to_tile(lng, lat, z):
    n = 2.0 ** z
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

# 高德地图服务器
AMAP_SERVERS = ["webrd01.is.autonavi.com", "webrd02.is.autonavi.com", "webrd03.is.autonavi.com", "webrd04.is.autonavi.com"]

# 预设城市坐标
PRESET_LOCATIONS = {
    "beijing": {"name": "北京", "lng": 116.3974, "lat": 39.9093, "country": "中国"},
    "shanghai": {"name": "上海", "lng": 121.4737, "lat": 31.2304, "country": "中国"},
    "guangzhou": {"name": "广州", "lng": 113.2644, "lat": 23.1291, "country": "中国"},
    "shenzhen": {"name": "深圳", "lng": 114.0579, "lat": 22.5431, "country": "中国"},
    "hangzhou": {"name": "杭州", "lng": 120.1551, "lat": 30.2741, "country": "中国"},
    "newyork": {"name": "纽约", "lng": -74.0060, "lat": 40.7128, "country": "美国"},
    "london": {"name": "伦敦", "lng": -0.1276, "lat": 51.5074, "country": "英国"},
    "tokyo": {"name": "东京", "lng": 139.6917, "lat": 35.6895, "country": "日本"}
}

# 用户位置缓存（模拟浏览器的位置记忆）
user_location_cache = {}

class LocationService:
    def __init__(self):
        self.geoip_reader = None
        self.init_geoip()
    
    def init_geoip(self):
        """初始化IP地理定位数据库"""
        try:
            # 尝试加载GeoIP数据库
            db_paths = [
                "GeoLite2-City.mmdb",
                "/usr/share/GeoIP/GeoLite2-City.mmdb",
                "/var/lib/GeoIP/GeoLite2-City.mmdb"
            ]
            
            for db_path in db_paths:
                if os.path.exists(db_path):
                    self.geoip_reader = geoip2.database.Reader(db_path)
                    logger.info(f"GeoIP数据库加载成功: {db_path}")
                    return
            
            logger.warning("未找到GeoIP数据库，将使用备用定位方案")
        except Exception as e:
            logger.error(f"GeoIP数据库加载失败: {e}")
    
    def get_location_by_ip(self, ip_address):
        """通过IP地址获取地理位置"""
        if not self.geoip_reader:
            return None
            
        try:
            response = self.geoip_reader.city(ip_address)
            return {
                'lng': response.location.longitude,
                'lat': response.location.latitude,
                'city': response.city.name if response.city.name else 'Unknown',
                'country': response.country.name if response.country.name else 'Unknown',
                'source': 'ip_geolocation'
            }
        except Exception as e:
            logger.warning(f"IP定位失败 {ip_address}: {e}")
            return None
    
    def get_location_by_html5(self, client_data):
        """处理HTML5 Geolocation API提供的位置"""
        try:
            lat = client_data.get('lat')
            lng = client_data.get('lng')
            accuracy = client_data.get('accuracy', 0)
            
            if lat and lng:
                return {
                    'lng': lng,
                    'lat': lat,
                    'accuracy': accuracy,
                    'source': 'html5_geolocation',
                    'timestamp': datetime.now().isoformat()
                }
        except Exception as e:
            logger.error(f"HTML5位置解析失败: {e}")
        return None
    
    def get_client_ip(self):
        """获取客户端真实IP（处理代理情况）"""
        # 检查常见的代理头
        proxy_headers = [
            'X-Forwarded-For',
            'X-Real-IP', 
            'X-Client-IP',
            'CF-Connecting-IP',  # Cloudflare
            'True-Client-IP'     # Akamai
        ]
        
        for header in proxy_headers:
            ip = request.headers.get(header)
            if ip:
                # 处理多个IP的情况（如 X-Forwarded-For: client, proxy1, proxy2）
                ips = [ip.strip() for ip in ip.split(',')]
                return ips[0]  # 第一个IP是客户端真实IP
        
        # 如果没有代理头，使用远程地址
        return request.remote_addr
    
    def determine_best_location(self, client_ip, html5_location=None):
        """确定最佳位置（混合定位策略）"""
        locations = []
        
        # 1. HTML5定位（最高优先级）
        if html5_location:
            locations.append(html5_location)
        
        # 2. IP定位
        ip_location = self.get_location_by_ip(client_ip)
        if ip_location:
            locations.append(ip_location)
        
        # 3. 选择最佳位置
        if locations:
            # 优先使用HTML5定位（更精确）
            for loc in locations:
                if loc.get('source') == 'html5_geolocation':
                    return loc
            # 否则使用IP定位
            return locations[0]
        
        # 4. 默认位置（北京）
        default_loc = PRESET_LOCATIONS['beijing'].copy()
        default_loc['source'] = 'default'
        return default_loc

location_service = LocationService()

@app.route("/")
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>高德地图瓦片代理 - 智能定位</title>
        <meta charset="utf-8">
        <style>
            #map { width: 100%; height: 500px; }
            .control-panel { 
                padding: 15px; 
                background: #f5f5f5; 
                margin-bottom: 10px;
                border-radius: 5px;
            }
            .location-select { 
                padding: 8px; 
                margin: 0 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            .btn { 
                padding: 8px 15px; 
                margin: 0 5px;
                background: #4CAF50; 
                color: white; 
                border: none; 
                border-radius: 4px; 
                cursor: pointer;
            }
            .btn:hover { background: #45a049; }
            .btn.secondary { background: #2196F3; }
            .btn.secondary:hover { background: #0b7dda; }
            .status { 
                margin: 10px 0; 
                padding: 10px; 
                background: #e7f3ff; 
                border-radius: 4px;
                font-size: 14px;
            }
            .location-info { 
                background: #d4edda; 
                border-left: 4px solid #28a745;
                padding: 8px 12px;
                margin: 5px 0;
            }
        </style>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
    </head>
    <body>
        <div class="control-panel">
            <h3>高德地图瓦片代理 - 智能定位系统</h3>
            
            <div>
                <button class="btn secondary" onclick="getAutoLocation()">🎯 自动定位</button>
                <button class="btn" onclick="useHighAccuracyLocation()">📡 精确定位</button>
                
                <select id="locationSelect" class="location-select">
                    <option value="">-- 手动选择城市 --</option>
                    <option value="beijing">北京</option>
                    <option value="shanghai">上海</option>
                    <option value="guangzhou">广州</option>
                    <option value="shenzhen">深圳</option>
                    <option value="hangzhou">杭州</option>
                    <option value="newyork">纽约</option>
                    <option value="london">伦敦</option>
                    <option value="tokyo">东京</option>
                </select>
                <button class="btn" onclick="setManualLocation()">确认选择</button>
            </div>
            
            <div id="status" class="status">
                点击"自动定位"获取您的位置，或手动选择城市
            </div>
            
            <div id="locationInfo" style="display: none;" class="location-info">
                <!-- 位置信息将在这里显示 -->
            </div>
        </div>
        
        <div id="map"></div>

        <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
        <script>
            let map;
            let currentMarker;
            let currentLocation = null;
            
            // 初始化地图
            function initMap(lng, lat, zoom = 10) {
                if (map) {
                    map.remove();
                }
                
                map = L.map('map').setView([lat, lng], zoom);
                
                // 添加高德地图图层
                L.tileLayer('/amap/{z}/{x}/{y}.jpg', {
                    attribution: '&copy; 高德地图'
                }).addTo(map);
                
                // 添加位置标记
                updateMarker(lat, lng);
                
                // 保存位置到缓存
                currentLocation = { lng, lat };
                localStorage.setItem('lastKnownLocation', JSON.stringify(currentLocation));
            }
            
            function updateMarker(lat, lng) {
                if (currentMarker) {
                    map.removeLayer(currentMarker);
                }
                currentMarker = L.marker([lat, lng]).addTo(map)
                    .bindPopup('您的位置')
                    .openPopup();
            }
            
            // 自动定位（IP定位 + 缓存）
            async function getAutoLocation() {
                showStatus('正在获取您的位置...', 'info');
                
                try {
                    const response = await fetch('/api/auto-location');
                    const data = await response.json();
                    
                    if (data.lng && data.lat) {
                        initMap(data.lng, data.lat, data.zoom || 12);
                        showLocationInfo(data);
                    } else {
                        throw new Error('定位失败');
                    }
                } catch (error) {
                    showStatus('自动定位失败，请尝试精确定位或手动选择', 'error');
                    console.error('Auto location failed:', error);
                }
            }
            
            // HTML5精确定位
            function useHighAccuracyLocation() {
                showStatus('正在请求精确定位权限...', 'info');
                
                if (!navigator.geolocation) {
                    showStatus('您的浏览器不支持地理定位', 'error');
                    return;
                }
                
                const options = {
                    enableHighAccuracy: true,
                    timeout: 10000,
                    maximumAge: 300000 // 5分钟缓存
                };
                
                navigator.geolocation.getCurrentPosition(
                    // 成功回调
                    async (position) => {
                        const lat = position.coords.latitude;
                        const lng = position.coords.longitude;
                        const accuracy = position.coords.accuracy;
                        
                        showStatus(`精确定位成功！精度: ${Math.round(accuracy)}米`, 'success');
                        
                        // 发送到服务器保存
                        await fetch('/api/save-location', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lat: lat,
                                lng: lng,
                                accuracy: accuracy
                            })
                        });
                        
                        initMap(lng, lat, 15);
                        showLocationInfo({
                            lng: lng,
                            lat: lat,
                            source: 'html5_geolocation',
                            accuracy: accuracy
                        });
                    },
                    // 错误回调
                    (error) => {
                        let message = '精确定位失败: ';
                        switch(error.code) {
                            case error.PERMISSION_DENIED:
                                message += '用户拒绝了定位请求';
                                break;
                            case error.POSITION_UNAVAILABLE:
                                message += '无法获取位置信息';
                                break;
                            case error.TIMEOUT:
                                message += '定位请求超时';
                                break;
                            default:
                                message += '未知错误';
                        }
                        showStatus(message, 'error');
                    },
                    options
                );
            }
            
            // 手动选择位置
            function setManualLocation() {
                const select = document.getElementById('locationSelect');
                const location = select.value;
                if (!location) {
                    alert('请选择一个城市');
                    return;
                }
                
                fetch(`/api/location/${location}`)
                    .then(r => r.json())
                    .then(data => {
                        initMap(data.lng, data.lat, 12);
                        showLocationInfo({
                            ...data,
                            source: 'manual_selection'
                        });
                    });
            }
            
            function showLocationInfo(locationData) {
                const infoDiv = document.getElementById('locationInfo');
                let html = `<strong>位置信息</strong><br>`;
                
                if (locationData.source === 'html5_geolocation') {
                    html += `📍 精确定位 (GPS/WiFi)<br>`;
                    html += `坐标: ${locationData.lng.toFixed(6)}, ${locationData.lat.toFixed(6)}<br>`;
                    if (locationData.accuracy) {
                        html += `精度: ±${Math.round(locationData.accuracy)}米`;
                    }
                } else if (locationData.source === 'ip_geolocation') {
                    html += `🌐 IP定位<br>`;
                    html += `位置: ${locationData.city || ''} ${locationData.country || ''}<br>`;
                    html += `坐标: ${locationData.lng.toFixed(6)}, ${locationData.lat.toFixed(6)}`;
                } else if (locationData.source === 'manual_selection') {
                    html += `👤 手动选择: ${locationData.name}<br>`;
                    html += `坐标: ${locationData.lng.toFixed(6)}, ${locationData.lat.toFixed(6)}`;
                }
                
                infoDiv.innerHTML = html;
                infoDiv.style.display = 'block';
            }
            
            function showStatus(message, type = 'info') {
                const statusDiv = document.getElementById('status');
                statusDiv.textContent = message;
                statusDiv.style.background = type === 'error' ? '#f8d7da' : 
                                           type === 'success' ? '#d4edda' : '#e7f3ff';
                statusDiv.style.borderLeft = type === 'error' ? '4px solid #dc3545' :
                                           type === 'success' ? '4px solid #28a745' : '4px solid #2196F3';
            }
            
            // 页面加载时尝试使用缓存位置
            window.addEventListener('load', () => {
                const lastLocation = localStorage.getItem('lastKnownLocation');
                if (lastLocation) {
                    const loc = JSON.parse(lastLocation);
                    initMap(loc.lng, loc.lat, 12);
                    showStatus('已恢复上次的位置', 'info');
                } else {
                    // 默认显示北京
                    initMap(116.3974, 39.9093, 10);
                }
                
                // 自动尝试定位
                setTimeout(getAutoLocation, 1000);
            });
        </script>
    </body>
    </html>
    """

# API路由
@app.route("/api/auto-location")
def auto_location():
    """自动定位接口"""
    client_ip = location_service.get_client_ip()
    
    # 检查是否有HTML5定位数据
    html5_location = None
    if request.args.get('lat') and request.args.get('lng'):
        html5_location = {
            'lat': float(request.args.get('lat')),
            'lng': float(request.args.get('lng')),
            'accuracy': float(request.args.get('accuracy', 0))
        }
    
    # 确定最佳位置
    location = location_service.determine_best_location(client_ip, html5_location)
    
    logger.info(f"自动定位 - IP: {client_ip}, 位置: {location}")
    
    return jsonify(location)

@app.route("/api/save-location", methods=['POST'])
def save_location():
    """保存HTML5定位结果"""
    try:
        data = request.json
        client_ip = location_service.get_client_ip()
        
        # 这里可以保存到数据库或文件
        logger.info(f"保存位置 - IP: {client_ip}, 位置: {data}")
        
        return jsonify({"status": "success", "message": "位置已保存"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/location/<location>")
def get_preset_location(location):
    if location in PRESET_LOCATIONS:
        return jsonify(PRESET_LOCATIONS[location])
    return jsonify({"error": "Location not found"}), 404

@app.route("/amap/<int:z>/<int:x>/<int:y>.jpg")
def get_tile(z, x, y):
    try:
        # 获取客户端位置信息
        client_ip = location_service.get_client_ip()
        base_location = location_service.determine_best_location(client_ip)
        
        base_lng = base_location['lng']
        base_lat = base_location['lat']
        
        # 计算瓦片中心坐标
        tile_center_lng, tile_center_lat = tile_to_lnglat(x, y, z)
        
        # 计算相对偏移并应用坐标转换
        offset_lng = tile_center_lng - base_lng
        offset_lat = tile_center_lat - base_lat
        
        target_lng = base_lng + offset_lng
        target_lat = base_lat + offset_lat
        
        # 坐标转换
        gcj_lng, gcj_lat = wgs84_to_gcj02(target_lng, target_lat)
        gcj_x, gcj_y = lnglat_to_tile(gcj_lng, gcj_lat, z)
        
        # 请求高德瓦片
        server_num = (gcj_x + gcj_y) % 4
        url = f"https://{AMAP_SERVERS[server_num]}/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={gcj_x}&y={gcj_y}&z={z}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.amap.com/"
        }
        
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code == 200:
            return send_file(BytesIO(r.content), mimetype="image/jpeg")
        else:
            return Response("Tile not found", status=404)
            
    except Exception as e:
        logger.error(f"获取瓦片失败: {e}")
        return Response("Service error", status=500)

@app.route("/health")
def health():
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8280, debug=True)