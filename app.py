import streamlit as st
from streamlit_folium import st_folium
import folium
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

# 라이브러리 에러 방지용 예외처리
try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    st.error("라이브러리 설치가 필요합니다. requirements.txt를 확인하세요.")
    get_geolocation = None

# ---------------------------------------------------------
# 1. 설정 및 UI
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI Realtime", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    .stButton>button { width: 100%; border-radius: 15px; height: 3.5em; font-weight: bold; }
    .status-box { padding: 10px; border-radius: 10px; background-color: #f0f2f6; margin-bottom: 10px; border-left: 5px solid #FF4B4B; }
</style>
""", unsafe_allow_html=True)

st.title("🏃 실시간 뚜벅이 네비 (v17.2)")

# 세션 상태 초기화
if 'end_point' not in st.session_state: st.session_state['end_point'] = None
if 'map_center' not in st.session_state: st.session_state['map_center'] = [37.5665, 126.9780]
if 'route_data' not in st.session_state: st.session_state['route_data'] = None
if 'last_pos' not in st.session_state: st.session_state['last_pos'] = None
if 'msg' not in st.session_state: st.session_state['msg'] = "GPS 신호를 기다리는 중..."

# ---------------------------------------------------------
# 2. 실시간 GPS 엔진 (안전 장치 추가)
# ---------------------------------------------------------
if get_geolocation:
    # key를 변경하여 캐시 문제 해결
    loc_data = get_geolocation(component_id=None, key="gps_v17_2")

    if loc_data and isinstance(loc_data, dict) and 'coords' in loc_data:
        lat = loc_data['coords']['latitude']
        lon = loc_data['coords']['longitude']
        st.session_state['last_pos'] = (lat, lon)
        
        # 최초 1회만 지도 중심 이동
        if 'init_gps' not in st.session_state:
            st.session_state['map_center'] = [lat, lon]
            st.session_state['init_gps'] = True
            st.rerun()

# ---------------------------------------------------------
# 3. 헬퍼 함수 (누락되었던 로직 복구)
# ---------------------------------------------------------
def get_nearest_subway_exit(point, radius=200):
    try:
        tags = {'railway': 'subway_entrance'}
        bbox = ox.utils_geo.bbox_from_point(point, dist=radius)
        entrances = ox.features_from_bbox(bbox=bbox, tags=tags)
        if entrances.empty: return None
        point_geom = Point(point[1], point[0])
        min_dist = float('inf')
        best_exit = None
        for idx, row in entrances.iterrows():
            exit_no = row.get('ref')
            if not exit_no: continue
            dist = row.geometry.centroid.distance(point_geom)
            if dist < min_dist:
                min_dist = dist
                best_exit = {'no': exit_no, 'coords': (row.geometry.centroid.y, row.geometry.centroid.x)}
        return best_exit
    except: return None

def calculate_pedestrian_weight(G_proj, danger_zone_proj):
    for u, v, k, data in G_proj.edges(keys=True, data=True):
        base_cost = data['length']
        penalty = 1.0
        
        highway = data.get('highway', '')
        if isinstance(highway, list): highway = highway[0]
        
        crossing = data.get('crossing', None)
        bridge = data.get('bridge', None)
        tunnel = data.get('tunnel', None)
        
        if highway == 'crossing' or crossing is not None: penalty = 0.1
        elif tunnel == 'yes' or bridge == 'yes' or highway == 'steps': penalty = 0.5
        elif highway in ['footway', 'pedestrian', 'path', 'living_street']: penalty = 0.8
        elif highway in ['motorway', 'trunk', 'primary', 'secondary']: penalty = 1.5
        
        if penalty > 0.2 and danger_zone_proj and 'geometry' in data:
            if data['geometry'].intersects(danger_zone_proj): penalty *= 5.0
            
        data['walk_cost'] = base_cost * penalty
    return G_proj

# ---------------------------------------------------------
# 4. 목적지 컨트롤
# ---------------------------------------------------------
col_search, col_btn = st.columns([3, 1])
with col_search:
    dest_query = st.text_input("목적지 검색", placeholder="예: 강남역", label_visibility="collapsed")
with col_btn:
    if st.button("🔍 이동"):
        if dest_query:
            try:
                coords = ox.geocode(dest_query)
                st.session_state['map_center'] = coords
                st.session_state['end_point'] = coords
                st.rerun()
            except: st.error("장소를 찾을 수 없습니다.")

col_nav, col_reset = st.columns([3, 1])
with col_nav:
    # GPS가 잡혔고(last_pos), 목적지(end_point)가 있을 때만 활성화
    nav_ready = st.session_state['last_pos'] is not None and st.session_state['end_point'] is not None
    nav_start = st.button("🚀 경로 안내 시작", type="primary", disabled=not nav_ready)

with col_reset:
    if st.button("🔄"):
        st.session_state['end_point'] = None
        st.session_state['route_data'] = None
        st.rerun()

# ---------------------------------------------------------
# 5. 경로 계산 엔진 (누락 없는 풀버전)
# ---------------------------------------------------------
if nav_start:
    if st.session_state['last_pos'] and st.session_state['end_point']:
        with st.spinner("최적 경로 분석 중..."):
            try:
                start = st.session_state['last_pos'] # 출발지는 내 위치!
                end = st.session_state['end_point']
                
                start_exit = get_nearest_subway_exit(start)
                end_exit = get_nearest_subway_exit(end)
                
                linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
                
                if linear_dist > 5000:
                    walk_time = int(linear_dist / 67)
                    st.session_state['route_data'] = {
                        'coords': [start, end], 'type': 'drone', 'time': walk_time, 
                        'dist': int(linear_dist), 'msg': "⚠️ 거리가 멀어 직선 안내합니다.", 
                        'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None
                    }
                else:
                    mid_lat = (start[0] + end[0]) / 2
                    mid_lon = (start[1] + end[1]) / 2
                    radius = linear_dist / 2 + 1000 
                    
                    ox.settings.timeout = 30
                    G = ox.graph_from_point((mid_lat, mid_lon), dist=radius, network_type='all')
                    G_proj = ox.project_graph(G)
                    
                    danger_poly_proj = None; danger_geojson = None
                    try:
                        tags = {'landuse': ['retail', 'commercial'], 'amenity': ['bar', 'pub', 'nightclub']}
                        bbox = ox.utils_geo.bbox_from_point((mid_lat, mid_lon), dist=radius)
                        gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
                        if not gdf.empty:
                            danger_poly_proj = gdf.to_crs(G_proj.graph['crs']).geometry.buffer(10).union_all()
                            danger_poly_vis = gdf.geometry.union_all()
                            if not danger_poly_vis.is_empty:
                                danger_geojson = gpd.GeoSeries([danger_poly_vis]).__geo_interface__
                    except: pass

                    G_proj = calculate_pedestrian_weight(G_proj, danger_poly_proj)
                    orig = ox.distance.nearest_nodes(G, start[1], start[0])
                    dest = ox.distance.nearest_nodes(G, end[1], end[0])
                    
                    if orig == dest:
                        walk_time = int(linear_dist / 67)
                        if walk_time < 1: walk_time = 1
                        st.session_state['route_data'] = {
                            'coords': [start, end], 'type': 'micro', 'time': walk_time, 'dist': int(linear_dist),
                            'msg': "✅ 바로 앞입니다!", 'danger_geojson': None, 'segments': [], 'special_points': [],
                            'subway_info': {'start': start_exit, 'end': end_exit}
                        }
                    else:
                        try:
                            route = nx.shortest_path(G_proj, orig, dest, weight='walk_cost')
                            res_coords = []; total_len = 0; segments = []; special_points = []
                            
                            for u, v in zip(route[:-1], route[1:]):
                                edge = G.get_edge_data(u, v)[0]
                                total_len += edge['length']
                                name = edge.get('name', ''); highway = edge.get('highway', '')
                                if isinstance(name, list): name = name[0]
                                if isinstance(highway, list): highway = highway[0]
                                
                                icon_type = None; point_coords = (G.nodes[u]['y'], G.nodes[u]['x'])
                                if not name:
                                    if highway == 'crossing': name = "횡단보도"; icon_type = 'traffic-light'
                                    elif highway == 'steps': name = "계단"; icon_type = 'sort-amount-asc'
                                    elif edge.get('tunnel') == 'yes': name = "지하도"; icon_type = 'road'
                                    elif edge.get('bridge') == 'yes': name = "육교"; icon_type = 'road'
                                    else: name = "골목길"
                                
                                if icon_type: special_points.append({'coords': point_coords, 'icon': icon_type, 'tooltip': name, 'color': 'orange'})
                                
                                seg_len = int(edge['length'])
                                if not segments or segments[-1]['name'] != name: segments.append({'name': name, 'len': seg_len})
                                else: segments[-1]['len'] += seg_len

                                if 'geometry' in edge:
                                    xs, ys = edge['geometry'].xy
                                    res_coords.extend(zip(ys, xs))
                                else:
                                    res_coords.append((G.nodes[u]['y'], G.nodes[u]['x'])); res_coords.append((G.nodes[v]['y'], G.nodes[v]['x']))
                            
                            walk_time = int(total_len / 67)
                            st.session_state['route_data'] = {
                                'coords': res_coords, 'type': 'normal', 'time': walk_time, 'dist': int(total_len),
                                'msg': "✅ 안내 시작", 'warning': False, 'danger_geojson': danger_geojson,
                                'segments': segments, 'special_points': special_points,
                                'subway_info': {'start': start_exit, 'end': end_exit}
                            }
                        except nx.NetworkXNoPath:
                             st.session_state['route_data'] = {'coords': [start, end], 'type': 'drone', 'time': 0, 'dist': int(linear_dist), 'msg': "길 없음", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
            except Exception as e: 
                st.error(f"계산 오류: {e}")

# ---------------------------------------------------------
# 6. 정보창 및 지도
# ---------------------------------------------------------
if st.session_state['last_pos']:
    st.write(f'<div class="status-box">📍 내 위치 수신 중 | 목적지: {dest_query if st.session_state["end_point"] else "미설정"}</div>', unsafe_allow_html=True)
else:
    st.warning("📡 GPS 신호 찾는 중... (위치 권한을 허용해주세요)")

if st.session_state['route_data']:
    data = st.session_state['route_data']
    with st.container():
        sub = data.get('subway_info')
        if sub and (sub['start'] or sub['end']):
            c_sub1, c_sub2 = st.columns(2)
            if sub['start']: c_sub1.info(f"🚇 출발: {sub['start']['no']}번 출구")
            if sub['end']: c_sub2.success(f"🏁 도착: {sub['end']['no']}번 출구")
            
        c1, c2, c3 = st.columns([1, 1, 2])
        c1.metric("시간", f"{data['time']}분")
        c2.metric("거리", f"{data['dist']}m")
        if data['type'] == 'normal': c3.success(data['msg'])
    
    if data['type'] == 'normal':
        with st.expander("📄 상세 경로 펼치기"):
            for idx, seg in enumerate(data['segments']):
                icon = "🚶"
                if "횡단보도" in seg['name']: icon = "🚦"
                elif "지하" in seg['name']: icon = "🚇"
                st.write(f"{idx+1}. {icon} {seg['name']} ({seg['len']}m)")

m = folium.Map(location=st.session_state['map_center'], zoom_start=17, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

if st.session_state['last_pos']:
    folium.CircleMarker(
        location=st.session_state['last_pos'],
        radius=10, color='white', fill=True, fill_color='#0078FF', fill_opacity=1, tooltip="내 위치"
    ).add_to(m)

if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag')).add_to(m)

if st.session_state.get('route_data') and st.session_state['route_data']['danger_geojson']:
    folium.GeoJson(st.session_state['route_data']['danger_geojson'], style_function=lambda x: {'color': 'orange', 'fillOpacity': 0.3}).add_to(m)

if st.session_state['route_data'] and st.session_state['route_data']['coords']:
    data = st.session_state['route_data']
    folium.PolyLine(data['coords'], color='green', weight=7, opacity=0.8).add_to(m)
    if data.get('special_points'):
        for sp in data['special_points']:
            folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
    m.fit_bounds(data['coords'])

output = st_folium(m, width=1000, height=500, key="main_map")
if output['last_clicked']:
    st.session_state['end_point'] = (output['last_clicked']['lat'], output['last_clicked']['lng'])
    st.rerun()
