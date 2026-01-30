import streamlit as st
from streamlit_folium import st_folium
import folium
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from gtts import gTTS # 음성 안내용
import base64
import io

# 라이브러리 안전 로딩
try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    get_geolocation = None

# ---------------------------------------------------------
# 1. 페이지 설정 & 스타일
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI Pro", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background-color: #ffffff; color: #000000; }
    [data-testid="stHeader"] { background-color: #ffffff; }
    .control-panel { background-color: #ffffff; padding: 15px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); margin-bottom: 10px; border: 1px solid #e0e0e0; }
    .stButton>button { background-color: #03C75A; color: white !important; border-radius: 8px; border: none; height: 3em; font-weight: bold; width: 100%; }
    .result-card { background-color: #ffffff; border-top: 1px solid #eee; padding: 20px; border-radius: 20px 20px 0 0; box-shadow: 0 -4px 10px rgba(0,0,0,0.05); margin-top: -20px; position: relative; z-index: 1000; }
    .block-container { padding-top: 0.5rem; padding-bottom: 5rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 상태 초기화
# ---------------------------------------------------------
if 'end_point' not in st.session_state: st.session_state['end_point'] = None
if 'map_center' not in st.session_state: st.session_state['map_center'] = [37.5665, 126.9780]
if 'route_data' not in st.session_state: st.session_state['route_data'] = None
if 'last_pos' not in st.session_state: st.session_state['last_pos'] = None
if 'gps_mode' not in st.session_state: st.session_state['gps_mode'] = True 
if 'facility_data' not in st.session_state: st.session_state['facility_data'] = [] # 편의시설 데이터

# ---------------------------------------------------------
# 3. 헬퍼 함수 (음성 & 편의시설)
# ---------------------------------------------------------
def text_to_speech(text):
    """텍스트를 음성(mp3)으로 변환하여 HTML 오디오 태그 반환"""
    try:
        tts = gTTS(text=text, lang='ko')
        mp3_fp = io.BytesIO()
        tts.write_to_fp(mp3_fp)
        mp3_fp.seek(0)
        b64 = base64.b64encode(mp3_fp.read()).decode()
        return f"""
            <audio autoplay="true" style="width: 100%;">
                <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
            </audio>
        """
    except: return ""

def get_facilities(point, radius=500):
    """주변 반경 내 화장실, 편의점, 카페 검색"""
    facilities = []
    try:
        tags = {'amenity': ['toilets', 'cafe'], 'shop': ['convenience']}
        bbox = ox.utils_geo.bbox_from_point(point, dist=radius)
        gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
        
        if not gdf.empty:
            for idx, row in gdf.iterrows():
                amenity = row.get('amenity')
                shop = row.get('shop')
                name = row.get('name', '이름 없음')
                
                lat, lon = None, None
                if hasattr(row.geometry, 'centroid'):
                    lat, lon = row.geometry.centroid.y, row.geometry.centroid.x
                else:
                    lat, lon = row.geometry.y, row.geometry.x
                
                icon, color = "question", "gray"
                if amenity == 'toilets': icon, color = "restroom", "blue"
                elif amenity == 'cafe': icon, color = "coffee", "brown"
                elif shop == 'convenience': icon, color = "shopping-cart", "orange"
                
                facilities.append({'coords': [lat, lon], 'name': name, 'icon': icon, 'color': color})
    except: pass
    return facilities

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

def calculate_pedestrian_weight(G_proj, danger_zone_proj, avoid_stairs=False, avoid_danger=False):
    for u, v, k, data in G_proj.edges(keys=True, data=True):
        base_cost = data['length']
        penalty = 1.0
        highway = data.get('highway', '')
        if isinstance(highway, list): highway = highway[0]
        crossing = data.get('crossing', None)
        
        if highway == 'crossing' or crossing is not None: penalty = 0.5 
        elif highway in ['footway', 'path', 'pedestrian', 'living_street']: penalty = 0.95
        elif highway in ['primary', 'secondary', 'tertiary', 'trunk']: penalty = 1.0 
        
        if highway == 'steps':
            if avoid_stairs: penalty = 100.0
            else: penalty = 1.5 
        if danger_zone_proj and 'geometry' in data:
            if data['geometry'].intersects(danger_zone_proj):
                if avoid_danger: penalty = 100.0
                else: penalty = 1.0
        data['walk_cost'] = base_cost * penalty
    return G_proj

# ---------------------------------------------------------
# 4. GPS 엔진
# ---------------------------------------------------------
if st.session_state['gps_mode'] and get_geolocation:
    try:
        loc_data = get_geolocation() 
        if loc_data and isinstance(loc_data, dict) and 'coords' in loc_data:
            lat = loc_data['coords']['latitude']
            lon = loc_data['coords']['longitude']
            st.session_state['last_pos'] = (lat, lon)
            if 'init_gps' not in st.session_state:
                st.session_state['map_center'] = [lat, lon]
                st.session_state['init_gps'] = True
                st.rerun()
    except: st.session_state['gps_mode'] = False

# ---------------------------------------------------------
# 5. UI 컨트롤 패널
# ---------------------------------------------------------
st.markdown('<div class="control-panel">', unsafe_allow_html=True)
col_input, col_go = st.columns([3, 1])

with col_input:
    dest_query = st.text_input("목적지 검색", placeholder="장소, 주소 입력", label_visibility="collapsed")

with col_go:
    if st.button("🔍"):
        if dest_query:
            try:
                coords = ox.geocode(dest_query)
                st.session_state['map_center'] = coords
                st.session_state['end_point'] = coords
                st.rerun()
            except: st.error("장소 미발견")

c_opt1, c_opt2, c_opt3 = st.columns(3)
with c_opt1: avoid_stairs = st.checkbox("계단 회피", value=False)
with c_opt2: avoid_danger = st.checkbox("유흥가 회피", value=False)
with c_opt3: show_facility = st.checkbox("🏪 편의시설", value=False) # [New]
st.markdown('</div>', unsafe_allow_html=True)

nav_ready = st.session_state['last_pos'] is not None and st.session_state['end_point'] is not None
if nav_ready:
    if st.button("🚀 경로안내 시작", type="primary"):
        with st.spinner("경로 계산 중..."):
            try:
                start = st.session_state['last_pos']
                end = st.session_state['end_point']
                start_exit = get_nearest_subway_exit(start)
                end_exit = get_nearest_subway_exit(end)
                linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
                
                # 편의시설 데이터 로딩 (옵션 켜져있을 때만)
                if show_facility:
                    st.session_state['facility_data'] = get_facilities(start, radius=500)
                else:
                    st.session_state['facility_data'] = []

                if linear_dist > 5000:
                    walk_time = int(linear_dist / 67)
                    st.session_state['route_data'] = {'coords': [start, end], 'type': 'drone', 'time': walk_time, 'dist': int(linear_dist), 'msg': "장거리 직선 안내", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
                else:
                    mid_lat = (start[0] + end[0]) / 2; mid_lon = (start[1] + end[1]) / 2
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
                            if not danger_poly_vis.is_empty: danger_geojson = gpd.GeoSeries([danger_poly_vis]).__geo_interface__
                    except: pass

                    G_proj = calculate_pedestrian_weight(G_proj, danger_poly_proj, avoid_stairs, avoid_danger)
                    orig = ox.distance.nearest_nodes(G, start[1], start[0])
                    dest = ox.distance.nearest_nodes(G, end[1], end[0])
                    
                    if orig == dest:
                        st.session_state['route_data'] = {'coords': [start, end], 'type': 'micro', 'time': 1, 'dist': int(linear_dist), 'msg': "도착!", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': {'start': start_exit, 'end': end_exit}}
                    else:
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
                                elif edge.get('tunnel') == 'yes' and edge.get('layer', 0) < 0: name = "지하보도"; icon_type = 'road'
                                else: name = "직진"
                            if icon_type: special_points.append({'coords': point_coords, 'icon': icon_type, 'tooltip': name, 'color': 'orange'})
                            seg_len = int(edge['length'])
                            if not segments or segments[-1]['name'] != name: segments.append({'name': name, 'len': seg_len})
                            else: segments[-1]['len'] += seg_len
                            if 'geometry' in edge: xs, ys = edge['geometry'].xy; res_coords.extend(zip(ys, xs))
                            else: res_coords.append((G.nodes[u]['y'], G.nodes[u]['x'])); res_coords.append((G.nodes[v]['y'], G.nodes[v]['x']))
                        
                        walk_time = int(total_len / 67)
                        st.session_state['route_data'] = {'coords': res_coords, 'type': 'normal', 'time': walk_time, 'dist': int(total_len), 'msg': "안내 중", 'danger_geojson': danger_geojson, 'segments': segments, 'special_points': special_points, 'subway_info': {'start': start_exit, 'end': end_exit}}
            except Exception as e: st.error(f"오류: {e}")

# ---------------------------------------------------------
# 6. 메인 지도
# ---------------------------------------------------------
m = folium.Map(location=st.session_state['map_center'], zoom_start=17, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

if st.session_state['last_pos']:
    folium.CircleMarker(location=st.session_state['last_pos'], radius=10, color='white', fill=True, fill_color='#03C75A', fill_opacity=1, tooltip="현위치").add_to(m)

if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag')).add_to(m)

# [New] 편의시설 아이콘 표시
if st.session_state.get('facility_data'):
    for fac in st.session_state['facility_data']:
        folium.Marker(
            location=fac['coords'],
            icon=folium.Icon(color=fac['color'], icon=fac['icon'], prefix='fa'),
            tooltip=fac['name']
        ).add_to(m)

if st.session_state.get('route_data'):
    data = st.session_state['route_data']
    if data['coords']:
        folium.PolyLine(data['coords'], color='#03C75A', weight=8, opacity=0.8).add_to(m)
        for sp in data['special_points']:
            folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
        m.fit_bounds(data['coords'])

output = st_folium(m, width="100%", height=700, key="main_map")

if output['last_clicked']:
    clicked = (output['last_clicked']['lat'], output['last_clicked']['lng'])
    if not st.session_state['last_pos']:
        st.session_state['last_pos'] = clicked
        st.rerun()
    else:
        st.session_state['end_point'] = clicked
        st.rerun()

# ---------------------------------------------------------
# 7. 하단 정보 패널 & 음성 재생
# ---------------------------------------------------------
if st.session_state['route_data']:
    data = st.session_state['route_data']
    
    # [New] 음성 안내 자동 재생
    speech_text = f"경로 안내를 시작합니다. 목적지까지 약 {data['time']}분, 거리는 {data['dist']}미터입니다."
    audio_html = text_to_speech(speech_text)
    st.markdown(audio_html, unsafe_allow_html=True) # 눈에 안 보이게 자동 재생

    st.markdown(f"""
    <div class="result-card">
        <h2 style="margin:0; color:#03C75A;">{data['time']}분 <span style="font-size:0.6em; color:#666;">({data['dist']}m)</span></h2>
        <p style="color:#333; margin-top:5px;">{data['msg']}</p>
    </div>
    """, unsafe_allow_html=True)
    
    with st.expander("📄 상세 경로"):
        for idx, seg in enumerate(data['segments']):
             icon = "🚶"
             if "횡단보도" in seg['name']: icon = "🚦"
             elif "지하" in seg['name']: icon = "🚇"
             st.write(f"{idx+1}. {icon} {seg['name']} ({seg['len']}m)")
