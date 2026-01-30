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
# 1. 디자인 (CSS)
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    .stApp, [data-testid="stAppViewContainer"] { background-color: #ffffff !important; }
    h1, h2, h3, h4, h5, h6, p, span, div, label, input { color: #000000 !important; }
    
    .stTextInput input {
        background-color: #f0f2f5 !important; 
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        border: 1px solid #ccc !important;
        border-radius: 8px !important;
        padding: 8px !important;
    }
    
    .stButton > button {
        background-color: #03C75A !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 8px !important;
        height: 40px !important;
        font-weight: bold !important;
        width: 100%;
    }
    .stButton > button p { color: #ffffff !important; }

    .result-card {
        background-color: #ffffff;
        border: 1px solid #ddd;
        border-radius: 12px;
        padding: 15px;
        box-shadow: 0 -2px 10px rgba(0,0,0,0.05);
        margin-top: 10px;
        border-left: 5px solid #03C75A;
    }
    
    .calorie-badge {
        background-color: #FFF8E1;
        color: #FF6F00 !important;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.8em;
        font-weight: bold;
        border: 1px solid #FFECB3;
        margin-left: 5px;
    }

    .block-container { 
        padding-top: 0.5rem !important; 
        padding-bottom: 2rem !important; 
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
if 'facility_data' not in st.session_state: st.session_state['facility_data'] = []
if 'manual_start' not in st.session_state: st.session_state['manual_start'] = False 

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

# [New] 시간 포맷팅 함수 (분 -> 시간 분)
def format_time(minutes):
    if minutes < 60:
        return f"{minutes}분"
    else:
        hours = minutes // 60
        mins = minutes % 60
        if mins == 0:
            return f"{hours}시간"
        return f"{hours}시간 {mins}분"

def calculate_calories(minutes):
    kcal = int(minutes * 3.3)
    if kcal < 1: kcal = 1
    return kcal

def is_same_location(loc1, loc2, threshold=0.0005):
    if not loc1 or not loc2: return False
    dist = np.sqrt((loc1[0]-loc2[0])**2 + (loc1[1]-loc2[1])**2)
    return dist < threshold

def get_facilities(point, radius=500, types=[]):
    facilities = []
    try:
        tags = {}
        if 'cvs' in types: tags.setdefault('shop', []).append('convenience')
        if 'wc' in types: tags.setdefault('amenity', []).append('toilets')
        if 'food' in types: tags.setdefault('amenity', []).extend(['restaurant', 'cafe', 'fast_food'])
        if not tags: return []

        bbox = ox.utils_geo.bbox_from_point(point, dist=radius)
        gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
        
        if not gdf.empty:
            for idx, row in gdf.iterrows():
                amenity = row.get('amenity'); shop = row.get('shop')
                name = row.get('name', '이름 없음')
                lat, lon = (row.geometry.centroid.y, row.geometry.centroid.x) if hasattr(row.geometry, 'centroid') else (row.geometry.y, row.geometry.x)
                icon, color = "question", "gray"
                if amenity == 'toilets': icon, color = "restroom", "blue"
                elif shop == 'convenience': icon, color = "shopping-cart", "orange"
                elif amenity in ['restaurant', 'cafe', 'fast_food']: icon, color = "cutlery", "red"
                facilities.append({'coords': [lat, lon], 'name': name, 'icon': icon, 'color': color})
    except: pass
    return facilities

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
        
        # 유흥가 회피 (Fallback을 위해 차단이 아닌 고가중치 부여)
        if avoid_danger and danger_zone_proj and 'geometry' in data:
            if data['geometry'].intersects(danger_zone_proj): penalty = 20.0 

        data['walk_cost'] = base_cost * penalty
    return G_proj

# ---------------------------------------------------------
# 4. GPS 엔진
# ---------------------------------------------------------
if get_geolocation:
    try:
        loc_data = get_geolocation() 
        if loc_data and isinstance(loc_data, dict) and 'coords' in loc_data:
            lat = loc_data['coords']['latitude']; lon = loc_data['coords']['longitude']
            st.session_state['last_pos'] = (lat, lon)
            
            if 'init_gps' not in st.session_state:
                st.session_state['map_center'] = [lat, lon]
                st.session_state['start_point'] = (lat, lon)
                st.session_state['init_gps'] = True
                st.rerun()
            elif not st.session_state['manual_start']: 
                st.session_state['start_point'] = (lat, lon)
                st.session_state['start_name'] = "내 위치"
    except: pass

# ---------------------------------------------------------
# 5. UI Layout
# ---------------------------------------------------------
# 입력창
c_start, c_end = st.columns([1, 1])
with c_start:
    val_s = st.session_state['start_name'] if st.session_state['manual_start'] else "내 위치"
    st.text_input("출발", value=val_s, disabled=True, key="disp_s")
    # 출발지 설정용 체크박스 (작게)
    set_start_mode = st.checkbox("📍 지도에서 출발지 찍기", value=False)

with c_end:
    dest_query = st.text_input("도착", placeholder="장소 검색", key="e_input")

# 버튼
c_search, c_reset = st.columns([3, 1])
with c_search:
    do_search = st.button("🔍 경로 탐색")
with c_reset:
    if st.button("🔄 복귀"):
        st.session_state['manual_start'] = False
        st.session_state['start_name'] = "내 위치"
        st.session_state['start_point'] = st.session_state['last_pos']
        st.rerun()

# 옵션
with st.expander("⚙️ 시설 및 경로 옵션", expanded=False):
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1: show_cvs = st.checkbox("🏪 편의점")
    with col_f2: show_wc = st.checkbox("🚽 화장실")
    with col_f3: show_food = st.checkbox("🍽️ 식당")
    st.divider()
    col_a1, col_a2 = st.columns(2)
    with col_a1: avoid_stairs = st.checkbox("계단 피하기")
    with col_a2: avoid_danger = st.checkbox("유흥가 우회", value=True)

# ---------------------------------------------------------
# 6. 로직 처리
# ---------------------------------------------------------
if do_search:
    if dest_query.strip():
        e_coords, e_name = get_coords_by_kakao(dest_query)
        if e_coords:
            st.session_state['end_point'] = e_coords
            st.session_state['end_name'] = e_name
    
    if st.session_state['start_point'] and st.session_state['end_point']:
        start = st.session_state['start_point']
        end = st.session_state['end_point']
        
        with st.spinner("최적 경로 계산 중..."):
            try:
                # 시설
                fac_types = []
                if show_cvs: fac_types.append('cvs')
                if show_wc: fac_types.append('wc')
                if show_food: fac_types.append('food')
                if fac_types: st.session_state['facility_data'] = get_facilities(start, radius=500, types=fac_types)
                else: st.session_state['facility_data'] = []

                linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
                
                # 장거리 (30km 이상)
                if linear_dist > 30000:
                    walk_time = int(linear_dist / 67)
                    dist_str = format_distance(int(linear_dist))
                    time_str = format_time(walk_time) # [New] 시간 변환
                    kcal = calculate_calories(walk_time)
                    st.session_state['route_data'] = {
                        'coords': [start, end], 'type': 'drone', 
                        'time_str': time_str, 'dist_str': dist_str, 
                        'kcal': kcal, 'msg': "장거리 직선 안내", 'danger_geojson': None, 'special_points': []
                    }
                else:
                    mid_lat = (start[0] + end[0]) / 2; mid_lon = (start[1] + end[1]) / 2
                    radius = linear_dist / 2 + 1000 
                    ox.settings.timeout = 30
                    G = ox.graph_from_point((mid_lat, mid_lon), dist=radius, network_type='all')
                    G_proj = ox.project_graph(G)
                    
                    danger_poly_proj = None; danger_geojson = None
                    try:
                        if avoid_danger:
                            tags = {'amenity': ['bar', 'pub', 'nightclub', 'karaoke']}
                            bbox = ox.utils_geo.bbox_from_point((mid_lat, mid_lon), dist=radius)
                            gdf = ox.features_from_bbox(bbox=bbox, tags=tags)
                            if not gdf.empty: 
                                danger_poly_proj = gdf.to_crs(G_proj.graph['crs']).geometry.buffer(15).union_all()
                                danger_poly_vis = gdf.geometry.union_all()
                                if not danger_poly_vis.is_empty: danger_geojson = gpd.GeoSeries([danger_poly_vis]).__geo_interface__
                    except: pass

                    G_proj = calculate_pedestrian_weight(G_proj, danger_poly_proj, avoid_stairs, avoid_danger)
                    orig = ox.distance.nearest_nodes(G, start[1], start[0]); dest = ox.distance.nearest_nodes(G, end[1], end[0])
                    
                    if orig == dest:
                        dist_str = format_distance(int(linear_dist))
                        st.session_state['route_data'] = {
                            'coords': [start, end], 'type': 'micro', 
                            'time_str': "1분", 'dist_str': dist_str, 'kcal': 1, 
                            'msg': "도착!", 'danger_geojson': None, 'special_points': []
                        }
                    else:
                        # [핵심] Fallback Logic
                        try:
                            route = nx.shortest_path(G_proj, orig, dest, weight='walk_cost')
                            msg = "안심 경로 안내 중"
                        except nx.NetworkXNoPath:
                            route = nx.shortest_path(G_proj, orig, dest, weight='length')
                            msg = "⚠️ 안전 경로가 없어 최단 경로로 안내합니다."
                        
                        res_coords = []; total_len = 0; special_points = []
                        for u, v in zip(route[:-1], route[1:]):
                            edge = G.get_edge_data(u, v)[0]
                            total_len += edge['length']
                            name = edge.get('name', ''); highway = edge.get('highway', ''); highway = highway[0] if isinstance(highway, list) else highway
                            icon_type = None; point_coords = (G.nodes[u]['y'], G.nodes[u]['x'])
                            if not name:
                                if highway == 'crossing': name = "횡단보도"; icon_type = 'traffic-light'
                                elif highway == 'steps': name = "계단"; icon_type = 'sort-amount-asc'
                                elif edge.get('tunnel') == 'yes' and edge.get('layer', 0) < 0: name = "지하보도"; icon_type = 'road'
                            if icon_type: special_points.append({'coords': point_coords, 'icon': icon_type, 'tooltip': name, 'color': 'orange'})
                            if 'geometry' in edge: xs, ys = edge['geometry'].xy; res_coords.extend(zip(ys, xs))
                            else: res_coords.append((G.nodes[u]['y'], G.nodes[u]['x'])); res_coords.append((G.nodes[v]['y'], G.nodes[v]['x']))
                        
                        walk_time = int(total_len / 67); dist_str = format_distance(int(total_len))
                        time_str = format_time(walk_time) # [New] 시간 변환
                        kcal = calculate_calories(walk_time)
                        st.session_state['route_data'] = {
                            'coords': res_coords, 'type': 'normal', 
                            'time_str': time_str, 'dist_str': dist_str, 
                            'kcal': kcal, 'msg': msg, 'danger_geojson': danger_geojson, 'special_points': special_points
                        }
            except Exception as e: st.error(f"경로 탐색 실패: {e}")

# ---------------------------------------------------------
# 7. 지도 표시
# ---------------------------------------------------------
m = folium.Map(location=st.session_state['map_center'], zoom_start=15, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

# 내 위치
if st.session_state['last_pos']:
    folium.CircleMarker(location=st.session_state['last_pos'], radius=8, color='white', fill=True, fill_color='#03C75A', fill_opacity=1, tooltip="내 현재 위치").add_to(m)

# 출발/도착 마커
if st.session_state['start_point']:
    folium.Marker(st.session_state['start_point'], icon=folium.Icon(color='blue', icon='play'), tooltip="출발지 (클릭 시 취소)").add_to(m)
if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag'), tooltip="도착지 (클릭 시 취소)").add_to(m)

if st.session_state.get('facility_data'):
    for fac in st.session_state['facility_data']: folium.Marker(location=fac['coords'], icon=folium.Icon(color=fac['color'], icon=fac['icon'], prefix='fa'), tooltip=fac['name']).add_to(m)

if st.session_state.get('route_data'):
    data = st.session_state['route_data']
    if data['danger_geojson']: folium.GeoJson(data['danger_geojson'], style_function=lambda x: {'color': 'red', 'fillColor': 'red', 'fillOpacity': 0.2}).add_to(m)
    if data['coords']:
        folium.PolyLine(data['coords'], color='#03C75A', weight=8, opacity=0.8).add_to(m)
        for sp in data['special_points']: folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
        m.fit_bounds(data['coords'])

output = st_folium(m, width="100%", height=400, key="main_map")

# [핵심] 스마트 클릭 & 취소 로직
if output['last_clicked']:
    clicked = (output['last_clicked']['lat'], output['last_clicked']['lng'])
    
    # 1. 출발지 모드 (체크박스 ON)
    if set_start_mode:
        if is_same_location(clicked, st.session_state['start_point']):
            # 같은 곳 클릭 -> 취소 (GPS 복귀)
            st.session_state['start_point'] = st.session_state['last_pos']
            st.session_state['start_name'] = "내 위치"
            st.session_state['manual_start'] = False
            st.toast("출발지 취소 (GPS 복귀)")
        else:
            st.session_state['start_point'] = clicked
            st.session_state['start_name'] = "지도 선택"
            st.session_state['manual_start'] = True
            st.toast("📍 출발지 설정")
        st.rerun()
        
    # 2. 도착지 모드 (기본값)
    else:
        if is_same_location(clicked, st.session_state['end_point']):
            # 같은 곳 클릭 -> 취소
            st.session_state['end_point'] = None
            st.session_state['end_name'] = ""
            st.toast("도착지 취소")
        else:
            st.session_state['end_point'] = clicked
            st.session_state['end_name'] = "지도 선택"
            st.toast("🏁 도착지 설정")
        st.rerun()

# 결과 카드 (시간 변환 적용)
if st.session_state['route_data']:
    data = st.session_state['route_data']
    st.markdown(f"""
    <div class="result-card">
        <h4 style="margin:0; color:#03C75A; display:flex; align-items:center;">
            {data['time_str']} ({data['dist_str']}) 
            <span class="calorie-badge">🔥 {data['kcal']} kcal</span>
        </h4>
        <p style="margin:5px 0 0 0; font-size:14px;">{st.session_state['start_name']} ➡️ {st.session_state['end_name']}</p>
        <p style="margin:0; font-size:12px; color:#666;">{data['msg']}</p>
    </div>
    """, unsafe_allow_html=True)
