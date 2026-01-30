import streamlit as st
from streamlit_folium import st_folium
import folium
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from gtts import gTTS 
import base64
import io
import requests

# 라이브러리 안전 로딩
try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    get_geolocation = None

# ---------------------------------------------------------
# 1. 디자인 (CSS) - 강력한 강제 적용 모드
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* 1. 전체 배경 강제 화이트 */
    .stApp {
        background-color: #ffffff !important;
    }
    [data-testid="stAppViewContainer"] {
        background-color: #ffffff !important;
    }
    
    /* 2. 입력창 디자인 (둥글게, 글씨 검정색 강제) */
    .stTextInput input {
        background-color: #f5f7f8 !important; /* 연한 회색 배경 */
        color: #000000 !important; /* 글씨는 무조건 검정 */
        border-radius: 12px !important;
        border: 1px solid #e0e0e0 !important;
        padding: 10px 15px !important;
    }
    /* 입력창 포커스 잡혔을 때 테두리 */
    .stTextInput input:focus {
        border: 2px solid #03C75A !important;
    }

    /* 3. 버튼 디자인 (네이버 그린) */
    .stButton > button {
        background-color: #03C75A !important;
        color: white !important;
        border: none !important;
        border-radius: 12px !important;
        font-weight: bold !important;
        height: 45px !important;
        width: 100% !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1) !important;
    }
    .stButton > button:hover {
        background-color: #02b351 !important;
    }

    /* 4. 체크박스 디자인 (글씨 검정 강제) */
    [data-testid="stCheckbox"] label {
        color: #333333 !important;
        font-weight: 500 !important;
    }
    
    /* 5. 결과 카드 (하단 정보창) */
    .result-card {
        background-color: white;
        border: 1px solid #eee;
        border-radius: 15px;
        padding: 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        margin-bottom: 20px;
        border-left: 6px solid #03C75A;
    }

    /* 6. 상단 여백 제거 및 헤더 숨김 */
    [data-testid="stHeader"] {display: none;}
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 2rem !important;
    }
    
    /* 오디오 플레이어 깔끔하게 */
    audio { width: 100%; height: 40px; margin-top: 10px; }
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
if 'facility_data' not in st.session_state: st.session_state['facility_data'] = []

# ---------------------------------------------------------
# 3. 헬퍼 함수
# ---------------------------------------------------------
def get_coords_by_kakao(query):
    try:
        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        headers = {"Authorization": "KakaoAK 035927af643bdbfe791b1639431879fc"} 
        params = {"query": query}
        response = requests.get(url, headers=headers, params=params)
        json_data = response.json()
        if json_data['documents']:
            best = json_data['documents'][0]
            return [float(best['y']), float(best['x'])], best['place_name']
        else: return None, None
    except: return None, None

def text_to_speech(text):
    try:
        tts = gTTS(text=text, lang='ko')
        mp3_fp = io.BytesIO()
        tts.write_to_fp(mp3_fp)
        mp3_fp.seek(0)
        b64 = base64.b64encode(mp3_fp.read()).decode()
        return f"""<audio controls autoplay><source src="data:audio/mp3;base64,{b64}" type="audio/mp3"></audio>"""
    except: return ""

def get_facilities(point, radius=500):
    facilities = []
    try:
        tags = {'amenity': ['toilets', 'cafe'], 'shop': ['convenience']}
        bbox = ox.utils_geo.bbox_from_point(point, dist=radius)
        gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
        if not gdf.empty:
            for idx, row in gdf.iterrows():
                amenity = row.get('amenity'); shop = row.get('shop')
                name = row.get('name', '이름 없음')
                lat, lon = (row.geometry.centroid.y, row.geometry.centroid.x) if hasattr(row.geometry, 'centroid') else (row.geometry.y, row.geometry.x)
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
            if dist < min_dist: min_dist = dist; best_exit = {'no': exit_no, 'coords': (row.geometry.centroid.y, row.geometry.centroid.x)}
        return best_exit
    except: return None

def calculate_pedestrian_weight(G_proj, danger_zone_proj, avoid_stairs=False, avoid_danger=False):
    for u, v, k, data in G_proj.edges(keys=True, data=True):
        base_cost = data['length']
        penalty = 1.0
        highway = data.get('highway', ''); highway = highway[0] if isinstance(highway, list) else highway
        crossing = data.get('crossing', None)
        if highway == 'crossing' or crossing is not None: penalty = 0.5 
        elif highway in ['footway', 'path', 'pedestrian', 'living_street']: penalty = 0.95
        elif highway in ['primary', 'secondary', 'tertiary', 'trunk']: penalty = 1.0 
        if highway == 'steps': penalty = 100.0 if avoid_stairs else 1.5 
        if danger_zone_proj and 'geometry' in data and data['geometry'].intersects(danger_zone_proj): penalty = 100.0 if avoid_danger else 1.0
        data['walk_cost'] = base_cost * penalty
    return G_proj

# ---------------------------------------------------------
# 4. GPS 엔진
# ---------------------------------------------------------
if st.session_state['gps_mode'] and get_geolocation:
    try:
        loc_data = get_geolocation() 
        if loc_data and isinstance(loc_data, dict) and 'coords' in loc_data:
            lat = loc_data['coords']['latitude']; lon = loc_data['coords']['longitude']
            st.session_state['last_pos'] = (lat, lon)
            if 'init_gps' not in st.session_state:
                st.session_state['map_center'] = [lat, lon]; st.session_state['init_gps'] = True; st.rerun()
    except: st.session_state['gps_mode'] = False

# ---------------------------------------------------------
# 5. UI Layout (네이버 지도 스타일)
# ---------------------------------------------------------
# [상단 검색 바]
col_input, col_go = st.columns([3, 1])
with col_input:
    dest_query = st.text_input("목적지 검색", placeholder="장소, 주소를 입력하세요", label_visibility="collapsed")
with col_go:
    if st.button("🔍 검색"):
        if dest_query:
            coords, place_name = get_coords_by_kakao(dest_query)
            if coords:
                st.session_state['map_center'] = coords
                st.session_state['end_point'] = coords
                st.toast(f"📍 '{place_name}' 이동")
                st.rerun()
            else: st.error("장소 미발견")

# [옵션 태그 영역]
with st.expander("⚙️ 경로/편의 옵션 열기", expanded=False):
    c1, c2, c3 = st.columns(3)
    with c1: avoid_stairs = st.checkbox("🪜 계단 제외", value=False)
    with c2: avoid_danger = st.checkbox("🛡️ 안심 귀가", value=False)
    with c3: show_facility = st.checkbox("🏪 편의시설", value=False)

# [안내 시작 버튼 - 경로 준비될 때만 표시]
nav_ready = st.session_state['last_pos'] is not None and st.session_state['end_point'] is not None
if nav_ready:
    if st.button("🚀 현위치에서 안내 시작 (Go)", type="primary"):
        with st.spinner("경로 찾는 중..."):
            try:
                start = st.session_state['last_pos']; end = st.session_state['end_point']
                start_exit = get_nearest_subway_exit(start); end_exit = get_nearest_subway_exit(end)
                linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
                
                if show_facility: st.session_state['facility_data'] = get_facilities(start, radius=500)
                else: st.session_state['facility_data'] = []

                if linear_dist > 5000:
                    walk_time = int(linear_dist / 67)
                    st.session_state['route_data'] = {'coords': [start, end], 'type': 'drone', 'time': walk_time, 'dist': int(linear_dist), 'msg': "거리가 멀어 직선 경로를 안내합니다.", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
                else:
                    mid_lat = (start[0] + end[0]) / 2; mid_lon = (start[1] + end[1]) / 2; radius = linear_dist / 2 + 1000 
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
                    orig = ox.distance.nearest_nodes(G, start[1], start[0]); dest = ox.distance.nearest_nodes(G, end[1], end[0])
                    
                    if orig == dest:
                        st.session_state['route_data'] = {'coords': [start, end], 'type': 'micro', 'time': 1, 'dist': int(linear_dist), 'msg': "목적지에 도착했습니다!", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': {'start': start_exit, 'end': end_exit}}
                    else:
                        route = nx.shortest_path(G_proj, orig, dest, weight='walk_cost')
                        res_coords = []; total_len = 0; segments = []; special_points = []
                        for u, v in zip(route[:-1], route[1:]):
                            edge = G.get_edge_data(u, v)[0]
                            total_len += edge['length']
                            name = edge.get('name', ''); highway = edge.get('highway', ''); highway = highway[0] if isinstance(highway, list) else highway
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
                        st.session_state['route_data'] = {'coords': res_coords, 'type': 'normal', 'time': walk_time, 'dist': int(total_len), 'msg': "경로 안내를 시작합니다.", 'danger_geojson': danger_geojson, 'segments': segments, 'special_points': special_points, 'subway_info': {'start': start_exit, 'end': end_exit}}
            except Exception as e: st.error(f"오류: {e}")

# ---------------------------------------------------------
# 6. 지도 및 결과창
# ---------------------------------------------------------
# 결과창을 지도 위가 아닌 아래에 배치 (모바일 친화적)
if st.session_state['route_data']:
    data = st.session_state['route_data']
    
    # 1. 음성 재생
    speech_text = f"목적지까지 {data['time']}분 걸립니다. {data['msg']}"
    st.markdown(text_to_speech(speech_text), unsafe_allow_html=True)
    
    # 2. 결과 카드
    st.markdown(f"""
    <div class="result-card">
        <h3 style="margin:0; color:#03C75A;">{data['time']}분 <span style="font-size:0.7em; color:#666; font-weight:normal;">({data['dist']}m)</span></h3>
        <p style="color:#333; margin:5px 0 0 0; font-size:0.9em;">{data['msg']}</p>
    </div>
    """, unsafe_allow_html=True)

# 지도 표시
m = folium.Map(location=st.session_state['map_center'], zoom_start=17, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

if st.session_state['last_pos']: folium.CircleMarker(location=st.session_state['last_pos'], radius=10, color='white', fill=True, fill_color='#03C75A', fill_opacity=1).add_to(m)
if st.session_state['end_point']: folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag')).add_to(m)
if st.session_state.get('facility_data'):
    for fac in st.session_state['facility_data']: folium.Marker(location=fac['coords'], icon=folium.Icon(color=fac['color'], icon=fac['icon'], prefix='fa'), tooltip=fac['name']).add_to(m)
if st.session_state.get('route_data') and st.session_state['route_data']['coords']:
    data = st.session_state['route_data']
    folium.PolyLine(data['coords'], color='#03C75A', weight=8, opacity=0.8).add_to(m)
    for sp in data['special_points']: folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
    m.fit_bounds(data['coords'])

output = st_folium(m, width="100%", height=600, key="main_map")

if output['last_clicked']:
    clicked = (output['last_clicked']['lat'], output['last_clicked']['lng'])
    if not st.session_state['last_pos']: st.session_state['last_pos'] = clicked; st.rerun()
    else: st.session_state['end_point'] = clicked; st.rerun()
