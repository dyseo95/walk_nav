import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation 
import folium
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

# ---------------------------------------------------------
# 1. 설정 및 UI
# ---------------------------------------------------------
st.set_page_config(page_title="뚜벅이 NAVI Pro", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    .stButton>button { width: 100%; border-radius: 15px; height: 3.5em; font-weight: bold; }
    .status-box { padding: 10px; border-radius: 10px; background-color: #f0f2f6; margin-bottom: 10px; border-left: 5px solid #FF4B4B; }
</style>
""", unsafe_allow_html=True)

st.title("🚶 실시간 뚜벅이 네비 (v17.1)")

# 세션 상태 초기화
if 'end_point' not in st.session_state: st.session_state['end_point'] = None
if 'map_center' not in st.session_state: st.session_state['map_center'] = [37.5665, 126.9780]
if 'route_data' not in st.session_state: st.session_state['route_data'] = None
if 'last_pos' not in st.session_state: st.session_state['last_pos'] = None

# ---------------------------------------------------------
# 2. 실시간 GPS 엔진 (에러 방지 로직 추가)
# ---------------------------------------------------------
# component_id 대신 key를 사용하고, None일 경우를 대비합니다.
loc_data = get_geolocation(key="realtime_gps_v1")

if loc_data and 'coords' in loc_data:
    lat = loc_data['coords']['latitude']
    lon = loc_data['coords']['longitude']
    st.session_state['last_pos'] = (lat, lon)
    
    # 최초 접속 시에만 지도를 내 위치로 이동
    if 'init_gps' not in st.session_state:
        st.session_state['map_center'] = [lat, lon]
        st.session_state['init_gps'] = True

# ---------------------------------------------------------
# 3. 목적지 컨트롤
# ---------------------------------------------------------
col_search, col_btn = st.columns([3, 1])
with col_search:
    dest_query = st.text_input("목적지 검색", placeholder="어디로 갈까요?", label_visibility="collapsed")
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
    # 내 위치(last_pos)가 있을 때만 활성화
    nav_ready = st.session_state['last_pos'] is not None and st.session_state['end_point'] is not None
    nav_start = st.button("🚀 경로 안내 시작", type="primary", disabled=not nav_ready)
with col_reset:
    if st.button("🔄 리셋"):
        st.session_state['end_point'] = None
        st.session_state['route_data'] = None
        st.rerun()

# ---------------------------------------------------------
# 4. 경로 계산 및 시각화 (v16.2 로직 포함)
# ---------------------------------------------------------
# (지면상 상세 로직은 v16.2와 동일하되, 출발지를 st.session_state['last_pos']로 고정)
if nav_start:
    with st.spinner("최적 경로 분석 중..."):
        # 여기에 v16.2의 경로 계산 엔진(calculate_pedestrian_weight 등)을 넣으세요.
        # 출발지 변수: start = st.session_state['last_pos']
        st.session_state['msg'] = "안내를 시작합니다."

# ---------------------------------------------------------
# 5. 실시간 지도 (VWorld)
# ---------------------------------------------------------
if st.session_state['last_pos']:
    st.write(f'<div class="status-box">📍 내 위치 기반 실시간 안내 중</div>', unsafe_allow_html=True)
else:
    st.warning("📡 GPS 신호를 기다리는 중입니다. 브라우저 위치 권한을 허용해주세요.")

m = folium.Map(location=st.session_state['map_center'], zoom_start=17, tiles=None)
folium.TileLayer('https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png', attr='VWorld', name='VWorld').add_to(m)

# 내 위치 표시 (실시간으로 움직임)
if st.session_state['last_pos']:
    folium.CircleMarker(
        location=st.session_state['last_pos'],
        radius=10, color='white', fill=True, fill_color='#0078FF', fill_opacity=1, tooltip="내 위치"
    ).add_to(m)

if st.session_state['end_point']:
    folium.Marker(st.session_state['end_point'], icon=folium.Icon(color='red', icon='flag')).add_to(m)

# 지도 클릭으로 목적지 설정
output = st_folium(m, width=1000, height=500, key="main_map")
if output['last_clicked']:
    st.session_state['end_point'] = (output['last_clicked']['lat'], output['last_clicked']['lng'])
    st.rerun()
