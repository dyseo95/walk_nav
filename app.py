import streamlit as st
from streamlit_folium import st_folium
import folium
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

# 라이브러리 안전 로딩
try:
    from streamlit_js_eval import get_geolocation
except ImportError:
    get_geolocation = None

# ---------------------------------------------------------
# 1. 설정 및 네이버 스타일 CSS 주입
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI", layout="wide", initial_sidebar_state="collapsed")

# [핵심] 네이버 지도 스타일 CSS
st.markdown("""
<style>
    /* 1. 상단 헤더 숨기기 및 여백 제거 (풀스크린 느낌) */
    header {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {
        padding-top: 0rem;
        padding-bottom: 2rem;
        padding-left: 0.5rem;
        padding-right: 0.5rem;
    }
    
    /* 2. 네이버 그린 버튼 스타일 */
    .stButton>button {
        background-color: #03C75A; /* 네이버 그린 */
        color: white;
        border: none;
        border-radius: 8px;
        height: 3em;
        font-weight: bold;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        transition: all 0.2s;
    }
    .stButton>button:hover {
        background-color: #02b351;
        color: white;
    }
    .stButton>button:active {
        background-color: #029f48;
        color: white;
    }

    /* 3. 검색창 스타일 (둥글게) */
    div[data-testid="stTextInput"]>div>div {
        border-radius: 20px;
        border: 2px solid #03C75A;
    }

    /* 4. 정보창 카드 스타일 */
    .info-card {
        background-color: white;
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 10px;
        border-left: 5px solid #03C75A;
    }
    
    /* 5. 옵션 박스 스타일 */
    div[data-testid="stExpander"] {
        border: none;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border-radius: 10px;
        background-color: #f8f9fa;
    }
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

# ---------------------------------------------------------
# 3. 실시간 GPS 엔진
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
    except:
        st.session_state['gps_mode'] = False

# ---------------------------------------------------------
# 4. 헬퍼 함수 (v19.1 알고리즘 유지)
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
# 5. UI 컨트롤 (네이버 지도 스타일 배치)
# ---------------------------------------------------------
# 상단 검색 바 (Floating Bar 느낌)
col_search, col_btn = st.columns([4, 1])
with col_search:
    dest_query = st.text_input("검색", placeholder="장소, 주소 검색", label_visibility="collapsed")
with col_btn:
    if st.button("🔍"):
        if dest_query:
            try:
                coords = ox.geocode(dest_query)
                st.session_state['map_center'] = coords
                st.session_state['end_point'] = coords
                st.rerun()
            except: st.error("장소를 찾지 못했어요.")

# 옵션 (접이식 메뉴)
with st.expander("⚙️ 경로 옵션", expanded=False):
    opt_col1, opt_col2 = st.columns(2)
    with opt_col1: avoid_stairs = st.checkbox("계단 피하기", value=False)
    with opt_col2: avoid_danger = st.checkbox("유흥가 피하기", value=False)

# ---------------------------------------------------------
# 6. 경로 계산 엔진
# ---------------------------------------------------------
# 하단 플로팅 액션 버튼 느낌의 레이아웃
nav_ready = st.session_state['last_pos'] is not None and st.session_state['end_point'] is not None

if nav_ready:
    if st.button("길찾기 시작 (Go)", type="primary"):
        with st.spinner("최적의 경로를 찾는 중..."):
            try:
                start = st.session_state['last_pos']
                end = st.session_state['end_point']
                start_exit = get_nearest_subway_exit(start)
                end_exit = get_nearest_subway_exit(end)
                linear_dist = np.sqrt((start[0]-end[0])**2 + (start[1]-end[1])**2) * 111000
                
                if linear_dist > 5000:
                    walk_time = int(linear_dist / 67)
                    st.session_state['route_data'] = {'coords': [start, end], 'type': 'drone', 'time': walk_time, 'dist': int(linear_dist), 'msg': "거리가 멀어 직선 안내", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': None}
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
                        walk_time = 1
                        st.session_state['route_data'] = {'coords': [start, end], 'type': 'micro', 'time': walk_time, 'dist': int(linear_dist), 'msg': "도착했습니다!", 'danger_geojson': None, 'segments': [], 'special_points': [], 'subway_info': {'start': start_exit, 'end': end_exit}}
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
                        st.session_state['route_data'] = {'coords': res_coords, 'type': 'normal', 'time': walk_time, 'dist': int(total_len), 'msg': "경로 안내 중", 'danger_geojson': danger_geojson, 'segments': segments, 'special_points': special_points, 'subway_info': {'start': start_exit, 'end': end_exit}}
            except Exception as e: st.error(f"경로를 찾을 수 없어요: {e}")
else:
    # GPS가 없거나 목적지가 없을 때 안내 문구
    if st.session_state['last_pos'] is None:
        st.info("📡 GPS 위치를 찾는 중입니다...")
    elif st.session_state['end_point'] is None:
        st.info("👆 지도에서 목적지를 검색하거나 클릭하세요.")

# ---------------------------------------------------------
# 7. 지도 및 정보창 (네이버 스타일)
# ---------------------------------------------------------
# 정보창 (카카오택시/네이버 지도 하단 패널 느낌)
if st.session_state['route_data']:
    data = st.session_state['route_data']
    st.markdown(f"""
    <div class="info-card">
        <h3 style="margin:0; color:#03C75A;">{data['time']}분 <span style="font-size:0.8em; color:gray;">({data['dist']}m)</span></h3>
        <p style="margin:0;">{data['msg']}</p>
    </div>
    """, unsafe_allow_html=True)
    
    with st.expander("상세 경로 보기"):
         for idx, seg in enumerate(data['segments']):
            icon = "🚶"
            if "횡단보도" in seg['name']: icon = "🚦"
            elif "지하" in seg['name']: icon = "🚇"
            st.write(f"{idx+1}. {icon} {seg['name']} ({seg['len']}m)")

m = folium.Map(location=st.session_state['map_center'], zoom_start=17, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

if st.session_state['last_pos']:
    folium.CircleMarker(location=st.session_state['last_pos'], radius=10, color='white', fill=True, fill_color='#03C75A', fill_opacity=1, tooltip="나").add_to(m)

if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag')).add_to(m)

if st.session_state.get('route_data'):
    data = st.session_state['route_data']
    if data['coords']:
        folium.PolyLine(data['coords'], color='#03C75A', weight=8, opacity=0.8).add_to(m) # 네이버 그린 경로선
        for sp in data['special_points']:
            folium.Marker(sp['coords'], icon=folium.Icon(color=sp['color'], icon=sp['icon'], prefix='fa'), tooltip=sp['tooltip']).add_to(m)
        m.fit_bounds(data['coords'])

output = st_folium(m, width=1000, height=600, key="main_map") # 지도 높이 키움

if output['last_clicked']:
    clicked_lat = output['last_clicked']['lat']
    clicked_lon = output['last_clicked']['lng']
    if not st.session_state['last_pos']:
        st.session_state['last_pos'] = (clicked_lat, clicked_lon)
        st.rerun()
    else:
        st.session_state['end_point'] = (clicked_lat, clicked_lon)
        st.rerun()
