import streamlit as st
from streamlit_folium import st_folium
import folium
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
import requests

# 라이브러리 안전 로딩
try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    get_geolocation = None

# ---------------------------------------------------------
# 1. 디자인 (CSS) - 모바일 최적화 & 블랙 텍스트
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
        border: 1px solid #ccc !important;
        border-radius: 8px !important;
        padding: 8px !important;
        font-size: 15px !important;
    }

    /* 버튼 스타일 */
    .stButton > button {
        background-color: #03C75A !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 8px !important;
        height: 40px !important;
        font-size: 15px !important;
        font-weight: bold !important;
    }
    .stButton > button p { color: #ffffff !important; }

    /* 결과 카드 */
    .result-card {
        background-color: #ffffff;
        border: 1px solid #ddd;
        border-radius: 12px;
        padding: 15px;
        box-shadow: 0 -2px 10px rgba(0,0,0,0.05);
        margin-top: 10px;
        border-left: 5px solid #03C75A;
    }
    
    /* 칼로리 뱃지 스타일 */
    .calorie-badge {
        background-color: #FFF8E1;
        color: #FF6F00 !important;
        padding: 3px 8px;
        border-radius: 10px;
        font-size: 0.8em;
        font-weight: bold;
        border: 1px solid #FFECB3;
        margin-left: 5px;
    }

    /* 모바일 여백 조정 */
    .block-container { 
        padding-top: 0.5rem !important; 
        padding-bottom: 2rem !important; 
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }
    
    /* 라디오 버튼 가로 정렬 */
    div[role="radiogroup"] {
        display: flex;
        flex-direction: row;
        justify-content: space-around;
        background-color: #f8f9fa;
        padding: 5px;
        border-radius: 8px;
        margin-bottom: 5px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 상태 초기화
# ---------------------------------------------------------
if 'start_point' not in st.session_state: st.session_state['start_point'] = None
if 'end_point' not in st.session_state: st.session_state['end_point'] = None
if 'start_name' not in st.session_state: st.session_state['start_name'] = "내 위치"
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
    if meters >= 1000: return f"{meters/1000:.1f}km"
    return f"{meters}m"

def calculate_calories(minutes):
    """
    보통 성인 걷기 칼로리 소모량: 약 3.5 kcal/분
    """
    kcal = int(minutes * 3.3)
    if kcal < 1: kcal = 1
    return kcal

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
        if avoid_danger and danger_zone_proj and 'geometry' in data:
            if data['geometry'].intersects(danger_zone_proj): penalty = 50.0 

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
# 5. UI Layout (입력창 + 클릭 모드 설정)
# ---------------------------------------------------------
# 입력창 2개
c_start, c_end = st.columns(2)
with c_start:
    start_query = st.text_input("출발지", placeholder="현위치", key="s_input")
with c_end:
    dest_query = st.text_input("도착지", placeholder="장소검색", key="e_input")

# 지도 클릭 모드 선택 (라디오 버튼)
click_option = st.radio("👇 지도 클릭 시 설정:", ["도착지 찍기", "출발지 찍기"], horizontal=True)

# 검색 버튼
if st.button("🔍 경로 탐색", type="primary"):
    # 출발지
    if start_query.strip():
        s_coords, s_name = get_coords_by_kakao(start_query)
        if s_coords:
            st.session_state['start_point'] = s_coords
            st.session_state['start_name'] = s_name
    else:
        if st.session_state['last_pos']:
            st.session_state['start_point'] = st.session_state['last_pos']
            st.session_state['start_name'] = "내 위치"

    # 도착지
    if dest_query.strip():
        e_coords, e_name = get_coords_by_kakao(dest_query)
        if e_coords:
            st.session_state['end_point'] = e_coords
            st.session_state['end_name'] = e_name
            if st.session_state['start_point']:
                mid_lat = (st.session_state['start_point'][0] + e_coords[0]) / 2
                mid_lon = (st.session_state['start_point'][1] + e_coords[1]) / 2
                st.session_state['map_center'] = [mid_lat, mid_lon]
            st.rerun()

# 옵션 (접기)
with st.expander("⚙️ 상세 옵션 (계단/유흥가)", expanded=False):
    c1, c2, c3 = st.columns(3)
    with c1: avoid_stairs = st.checkbox("계단 ❌", value=False)
    with c2: avoid_danger = st.checkbox("유흥가 ❌", value=False)
    with c3: show_facility = st.checkbox("편의점 🏪", value=False)

# ---------------------------------------------------------
# 6. 경로 계산
# ---------------------------------------------------------
calc_ready = st.session_state['start_point'] is not None and st.session_state['end_point'] is not None

if calc_ready:
    start = st.session_state['start_point']
    end = st.session_state['end_point']
    
    with st.spinner("계산 중..."):
        try:
            linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
            
            if show_facility: st.session_state['facility_data'] = get_facilities(start, radius=500)
            else: st.session_state['facility_data'] = []

            if linear_dist > 30000:
                walk_time = int(linear_dist / 67); dist_str = format_distance(int(linear_dist))
                kcal = calculate_calories(walk_time) # 칼로리 계산
                st.session_state['route_data'] = {'coords': [start, end], 'type': 'drone', 'time': walk_time, 'dist': int(linear_dist), 'dist_str': dist_str, 'kcal': kcal, 'msg': "거리가 멀어 직선 안내", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
            else:
                mid_lat = (start[0] + end[0]) / 2; mid_lon = (start[1] + end[1]) / 2
                radius = linear_dist / 2 + 1000 
                ox.settings.timeout = 60
                G = ox.graph_from_point((mid_lat, mid_lon), dist=radius, network_type='all')
                G_proj = ox.project_graph(G)
                
                danger_poly_proj = None; danger_geojson = None
                try:
                    if avoid_danger:
                        tags = {'amenity': ['bar', 'pub', 'nightclub', 'karaoke', 'biergarten']}
                        bbox = ox.utils_geo.bbox_from_point((mid_lat, mid_lon), dist=radius)
                        gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
                        if not gdf.empty: 
                            danger_poly_proj = gdf.to_crs(G_proj.graph['crs']).geometry.buffer(20).union_all()
                            danger_poly_vis = gdf.geometry.union_all()
                            if not danger_poly_vis.is_empty: danger_geojson = gpd.GeoSeries([danger_poly_vis]).__geo_interface__
                except: pass

                G_proj = calculate_pedestrian_weight(G_proj, danger_poly_proj, avoid_stairs, avoid_danger)
                orig = ox.distance.nearest_nodes(G, start[1], start[0]); dest = ox.distance.nearest_nodes(G, end[1], end[0])
                
                if orig == dest:
                    dist_str = format_distance(int(linear_dist))
                    kcal = 1
                    st.session_state['route_data'] = {'coords': [start, end], 'type': 'micro', 'time': 1, 'dist': int(linear_dist), 'dist_str': dist_str, 'kcal': kcal, 'msg': "도착!", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
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
                    
                    walk_time = int(total_len / 67); dist_str = format_distance(int(total_len))
                    kcal = calculate_calories(walk_time) # 칼로리
                    s_exit = get_nearest_subway_exit(start); e_exit = get_nearest_subway_exit(end)
                    st.session_state['route_data'] = {'coords': res_coords, 'type': 'normal', 'time': walk_time, 'dist': int(total_len), 'dist_str': dist_str, 'kcal': kcal, 'msg': "안내 중", 'danger_geojson': danger_geojson, 'segments': segments, 'special_points': special_points, 'subway_info': {'start': s_exit, 'end': e_exit}}
        except Exception as e: st.error(f"실패: {e}")

# ---------------------------------------------------------
# 7. 지도 및 결과 표시
# ---------------------------------------------------------
m = folium.Map(location=st.session_state['map_center'], zoom_start=15, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

# 내 위치
if st.session_state['last_pos']:
    folium.CircleMarker(location=st.session_state['last_pos'], radius=10, color='white', fill=True, fill_color='#03C75A', fill_opacity=1, tooltip="내 현재 위치").add_to(m)

# 출발/도착 마커
if st.session_state['start_point']:
    folium.Marker(st.session_state['start_point'], icon=folium.Icon(color='blue', icon='play'), tooltip="출발").add_to(m)
if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag'), tooltip="도착").add_to(m)

if st.session_state.get('facility_data'):
    for fac in st.session_state['facility_data']: folium.Marker(location=fac['coords'], icon=folium.Icon(color=fac['color'], icon=fac['icon'], prefix='fa'), tooltip=fac['name']).add_to(m)

if st.session_state.get('route_data'):
    data = st.session_state['route_data']
    if data['danger_geojson']: folium.GeoJson(data['danger_geojson'], style_function=lambda x: {'color': 'red', 'fillColor': 'red', 'fillOpacity': 0.2}).add_to(m)
    if data['coords']:
        folium.PolyLine(data['coords'], color='#03C75A', weight=8, opacity=0.8).add_to(m)
        for sp in data['special_points']: folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
        m.fit_bounds(data['coords'])

# 지도 (클릭 활성화)
output = st_folium(m, width="100%", height=400, key="main_map")

# [핵심] 지도 클릭 로직
if output['last_clicked']:
    clicked = (output['last_clicked']['lat'], output['last_clicked']['lng'])
    if click_option == "도착지 찍기":
        st.session_state['end_point'] = clicked; st.session_state['end_name'] = "지도 선택"
        st.toast("🏁 도착지를 설정했습니다.")
        st.rerun()
    else:
        st.session_state['start_point'] = clicked; st.session_state['start_name'] = "지도 선택"
        st.toast("📍 출발지를 설정했습니다.")
        st.rerun()

# 결과 카드 (칼로리 추가)
if st.session_state['route_data']:
    data = st.session_state['route_data']
    st.markdown(f"""
    <div class="result-card">
        <h4 style="margin:0; color:#03C75A; display:flex; align-items:center;">
            {data['time']}분 ({data['dist_str']}) 
            <span class="calorie-badge">🔥 {data['kcal']} kcal</span>
        </h4>
        <p style="margin:5px 0 0 0; font-size:14px;">{st.session_state['start_name']} ➡️ {st.session_state['end_name']}</p>
    </div>
    """, unsafe_allow_html=True)
