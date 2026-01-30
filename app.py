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
# 1. 디자인 (CSS) - 글씨 블랙 강제 & UI 최적화
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* 배경 & 텍스트 강제 설정 */
    .stApp, [data-testid="stAppViewContainer"] { background-color: #ffffff !important; }
    h1, h2, h3, h4, h5, h6, p, span, div, label, input { color: #000000 !important; }
    
    /* 입력창 스타일 */
    .stTextInput input {
        background-color: #f0f2f5 !important; 
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        caret-color: #000000 !important;
        border: 1px solid #ccc !important;
        border-radius: 12px !important;
    }

    /* 버튼 스타일 */
    .stButton > button {
        background-color: #03C75A !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 10px !important;
        height: 45px !important;
    }
    .stButton > button p { color: #ffffff !important; }

    /* 결과 카드 */
    .result-card {
        background-color: #ffffff;
        border: 1px solid #ddd;
        border-radius: 15px;
        padding: 20px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.1);
        margin-bottom: 20px;
        border-left: 5px solid #03C75A;
    }
    
    .block-container { padding-top: 1rem !important; padding-bottom: 5rem !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 상태 초기화
# ---------------------------------------------------------
if 'start_point' not in st.session_state: st.session_state['start_point'] = None # [New] 출발지 좌표
if 'end_point' not in st.session_state: st.session_state['end_point'] = None
if 'start_name' not in st.session_state: st.session_state['start_name'] = "내 위치" # [New] 출발지 이름
if 'end_name' not in st.session_state: st.session_state['end_name'] = ""
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
        headers = {"Authorization": "KakaoAK 035927af643bdbfe791b1639431879fc"} 
        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        params = {"query": query}
        response = requests.get(url, headers=headers, params=params)
        json_data = response.json()
        if json_data['documents']:
            best = json_data['documents'][0]
            return [float(best['y']), float(best['x'])], best['place_name']
        else: return None, None
    except: return None, None

def format_distance(meters):
    """[New] 거리 단위 변환 함수 (m -> km)"""
    if meters >= 1000:
        return f"{meters/1000:.1f}km"
    return f"{meters}m"

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
        
        if highway == 'service': penalty = 1.5 
        elif highway == 'crossing' or crossing is not None: penalty = 0.5 
        elif highway in ['footway', 'path', 'pedestrian', 'living_street']: penalty = 0.9 
        elif highway in ['primary', 'secondary', 'tertiary', 'trunk']: penalty = 1.0 
        
        if highway == 'steps': penalty = 100.0 if avoid_stairs else 2.0 
        if danger_zone_proj and 'geometry' in data and data['geometry'].intersects(danger_zone_proj): penalty = 100.0 if avoid_danger else 1.0
        data['walk_cost'] = base_cost * penalty
    return G_proj

# ---------------------------------------------------------
# 4. GPS 및 위치 설정
# ---------------------------------------------------------
if st.session_state['gps_mode'] and get_geolocation:
    try:
        loc_data = get_geolocation() 
        if loc_data and isinstance(loc_data, dict) and 'coords' in loc_data:
            lat = loc_data['coords']['latitude']; lon = loc_data['coords']['longitude']
            st.session_state['last_pos'] = (lat, lon)
            # 앱 켜지면 일단 내 위치를 지도 중심으로
            if 'init_gps' not in st.session_state:
                st.session_state['map_center'] = [lat, lon]; st.session_state['init_gps'] = True; st.rerun()
    except: st.session_state['gps_mode'] = False

# ---------------------------------------------------------
# 5. UI Layout (A to B 입력창)
# ---------------------------------------------------------
# [New] 출발지 / 도착지 입력창 분리
c_start, c_end = st.columns(2)
with c_start:
    start_query = st.text_input("출발지 (비워두면 내 위치)", placeholder="예: 강남역 (비워두면 GPS)", key="s_input")
with c_end:
    dest_query = st.text_input("도착지", placeholder="예: 스타벅스 홍대점", key="e_input")

# 검색 및 경로 설정 버튼
if st.button("🔍 경로 탐색 (Search)", type="primary"):
    # 1. 출발지 설정 로직
    if start_query.strip():
        # 사용자가 출발지를 입력한 경우
        s_coords, s_name = get_coords_by_kakao(start_query)
        if s_coords:
            st.session_state['start_point'] = s_coords
            st.session_state['start_name'] = s_name
        else:
            st.error(f"출발지 '{start_query}'를 찾을 수 없습니다.")
    else:
        # 비워둔 경우 -> 내 위치(GPS) 사용
        if st.session_state['last_pos']:
            st.session_state['start_point'] = st.session_state['last_pos']
            st.session_state['start_name'] = "내 위치"
        else:
            st.error("GPS 신호가 없고 출발지도 입력되지 않았습니다.")

    # 2. 도착지 설정 로직
    if dest_query.strip():
        e_coords, e_name = get_coords_by_kakao(dest_query)
        if e_coords:
            st.session_state['end_point'] = e_coords
            st.session_state['end_name'] = e_name
            # 지도 중심을 출발지-도착지 중간으로 이동
            if st.session_state['start_point']:
                mid_lat = (st.session_state['start_point'][0] + e_coords[0]) / 2
                mid_lon = (st.session_state['start_point'][1] + e_coords[1]) / 2
                st.session_state['map_center'] = [mid_lat, mid_lon]
            st.rerun()
        else:
            st.error(f"도착지 '{dest_query}'를 찾을 수 없습니다.")

# 옵션 태그
with st.expander("⚙️ 상세 옵션", expanded=False):
    c1, c2, c3 = st.columns(3)
    with c1: avoid_stairs = st.checkbox("계단 피하기", value=False)
    with c2: avoid_danger = st.checkbox("유흥가 우회", value=False)
    with c3: show_facility = st.checkbox("편의시설 표시", value=False)

# ---------------------------------------------------------
# 6. 경로 계산 엔진
# ---------------------------------------------------------
# 출발지와 도착지가 모두 세팅되어야 계산 시작
calc_ready = st.session_state['start_point'] is not None and st.session_state['end_point'] is not None

if calc_ready:
    # 자동 계산 트리거 (버튼 없이 로직 진행하거나, 명시적 버튼 둘 수 있음. 여기선 바로 계산)
    start = st.session_state['start_point']
    end = st.session_state['end_point']
    
    # 이전 계산과 같으면 재계산 방지 (Session State 활용)
    # 여기서는 단순화를 위해 매번 렌더링 시 계산하지만, 실제론 route_data가 있으면 스킵 가능
    # 다만 옵션 변경 시 재계산이 필요하므로 그대로 둡니다.

    with st.spinner(f"{st.session_state['start_name']} ➡️ {st.session_state['end_name']} 경로 계산 중..."):
        try:
            linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
            
            # [New] 장거리 제한을 30km(30000m)로 완화
            if linear_dist > 30000:
                walk_time = int(linear_dist / 67) # 평균 시속 4km/h 가정
                dist_str = format_distance(int(linear_dist))
                st.session_state['route_data'] = {
                    'coords': [start, end], 'type': 'drone', 
                    'time': walk_time, 'dist': int(linear_dist), 'dist_str': dist_str,
                    'msg': "거리가 너무 멀어(30km+) 직선 경로만 표시합니다.", 
                    'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None
                }
            else:
                # 편의시설
                if show_facility: st.session_state['facility_data'] = get_facilities(start, radius=500)
                else: st.session_state['facility_data'] = []

                # 그래프 다운로드 반경 설정 (거리의 반 + 여유분)
                mid_lat = (start[0] + end[0]) / 2
                mid_lon = (start[1] + end[1]) / 2
                radius = linear_dist / 2 + 1000 
                
                ox.settings.timeout = 60 # 장거리는 시간 더 줌
                G = ox.graph_from_point((mid_lat, mid_lon), dist=radius, network_type='all')
                G_proj = ox.project_graph(G)
                
                danger_poly_proj = None; danger_geojson = None
                # (유흥가 로직 생략 - 장거리 시 렉 방지 위해 try/except로 가볍게 처리)
                try:
                    if avoid_danger: # 옵션 켰을 때만 로딩
                        tags = {'landuse': ['retail', 'commercial'], 'amenity': ['bar', 'pub', 'nightclub']}
                        bbox = ox.utils_geo.bbox_from_point((mid_lat, mid_lon), dist=radius)
                        gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
                        if not gdf.empty:
                            danger_poly_proj = gdf.to_crs(G_proj.graph['crs']).geometry.buffer(10).union_all()
                except: pass

                G_proj = calculate_pedestrian_weight(G_proj, danger_poly_proj, avoid_stairs, avoid_danger)
                orig = ox.distance.nearest_nodes(G, start[1], start[0])
                dest = ox.distance.nearest_nodes(G, end[1], end[0])
                
                if orig == dest:
                    dist_str = format_distance(int(linear_dist))
                    st.session_state['route_data'] = {'coords': [start, end], 'type': 'micro', 'time': 1, 'dist': int(linear_dist), 'dist_str': dist_str, 'msg': "도착!", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
                else:
                    route = nx.shortest_path(G_proj, orig, dest, weight='walk_cost')
                    res_coords = []; total_len = 0; segments = []; special_points = []
                    
                    for u, v in zip(route[:-1], route[1:]):
                        edge = G.get_edge_data(u, v)[0]
                        total_len += edge['length']
                        # (아이콘 로직 동일)
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
                    dist_str = format_distance(int(total_len))
                    
                    # 지하철 출구 정보 (출발/도착지가 바뀌었으므로 다시 계산)
                    s_exit = get_nearest_subway_exit(start)
                    e_exit = get_nearest_subway_exit(end)
                    
                    st.session_state['route_data'] = {
                        'coords': res_coords, 'type': 'normal', 
                        'time': walk_time, 'dist': int(total_len), 'dist_str': dist_str,
                        'msg': "안내 중", 'danger_geojson': None, 
                        'segments': segments, 'special_points': special_points, 
                        'subway_info': {'start': s_exit, 'end': e_exit}
                    }
        except Exception as e: st.error(f"경로 계산 실패 (너무 멀거나 길이 없음): {e}")

# ---------------------------------------------------------
# 7. 결과 표시
# ---------------------------------------------------------
if st.session_state['route_data']:
    data = st.session_state['route_data']
    
    # 음성 안내
    speech_text = f"목적지까지 약 {data['time']}분, 거리는 {data['dist_str']}입니다."
    st.markdown(text_to_speech(speech_text), unsafe_allow_html=True)
    
    # 결과 카드
    st.markdown(f"""
    <div class="result-card">
        <h3 style="margin:0; color:#03C75A;">{data['time']}분 <span style="font-size:0.8em; color:#666; font-weight:normal;">({data['dist_str']})</span></h3>
        <p style="color:#333; margin:5px 0 0 0; font-size:0.9em;">{st.session_state['start_name']} ➡️ {st.session_state['end_name']}</p>
    </div>
    """, unsafe_allow_html=True)

# 지도
m = folium.Map(location=st.session_state['map_center'], zoom_start=15, tiles=None) # 줌 레벨 조정
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

# 출발/도착 마커
if st.session_state['start_point']:
    folium.Marker(st.session_state['start_point'], icon=folium.Icon(color='blue', icon='play'), tooltip="출발").add_to(m)
if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag'), tooltip="도착").add_to(m)

# 경로 및 편의시설
if st.session_state.get('facility_data'):
    for fac in st.session_state['facility_data']: folium.Marker(location=fac['coords'], icon=folium.Icon(color=fac['color'], icon=fac['icon'], prefix='fa'), tooltip=fac['name']).add_to(m)
if st.session_state.get('route_data') and st.session_state['route_data']['coords']:
    data = st.session_state['route_data']
    folium.PolyLine(data['coords'], color='#03C75A', weight=8, opacity=0.8).add_to(m)
    for sp in data['special_points']: folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
    m.fit_bounds(data['coords'])

output = st_folium(m, width="100%", height=600, key="main_map")
