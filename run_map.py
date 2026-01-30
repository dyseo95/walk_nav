import osmnx as ox
import networkx as nx
import folium

def run_pedestrian_nav():
    print("=== 뚜벅이 네비게이션 v5: '진짜' 지름길 강제 연결 버전 ===")

    place_name = "Myeong-dong, Seoul, South Korea"
    print(f"1. 지도를 불러오는 중입니다... ({place_name})")
    
    # ⚠️ [수정] 'all' 대신 다시 'walk'를 씁니다. (고립된 노드 방지)
    G = ox.graph_from_place(place_name, network_type="walk", simplify=True)
    
    # [안전장치] 혹시 끊겨있는 길(고립된 섬)이 있다면 제거하고 가장 큰 덩어리만 남김
    # (에러 방지용 핵심 코드)
    try:
        G = ox.utils_graph.get_largest_component(G, strongly=True)
    except:
        pass # 구버전 osmnx 호환용

    G_proj = ox.project_graph(G)

    # 2. 사용자 입력
    print("\n[검색 예시: 명동역, 명동성당]")
    start_query = input("🟢 출발지: ") + " 서울"
    end_query = input("🔴 도착지: ") + " 서울"

    try:
        start_latlng = ox.geocode(start_query)
        end_latlng = ox.geocode(end_query)
        print(f"   -> 좌표 변환 완료: {start_latlng} ~ {end_latlng}")
    except:
        print("❌ 장소를 찾을 수 없습니다.")
        return

    # =========================================================
    # ⚡ [Magic Shortcut] 님의 눈에만 보이는 '개구멍' 뚫기
    # =========================================================
    # 명동역 8/9번 출구 뒷골목(A) <---> 성당 앞 주차장(B)을 잇는 가상의 선
    # 이 좌표가 있으면 벽을 뚫고 지나갑니다.
    shortcut_start = (37.5615, 126.9855) 
    shortcut_end = (37.5628, 126.9865)   
    
    # 1. 지도(Graph)에서 위 좌표와 가장 가까운 '진짜 길'의 점(Node)을 찾음
    node_a = ox.distance.nearest_nodes(G_proj, shortcut_start[1], shortcut_start[0]) # 위도/경도 순서 주의
    node_b = ox.distance.nearest_nodes(G_proj, shortcut_end[1], shortcut_end[0])
    
    # 2. 두 점을 잇는 가상의 길(Edge) 추가
    # length(거리)를 1미터로 사기쳐서 무조건 이리로 가게 만듦
    G_proj.add_edge(node_a, node_b, weight=0.01, length=1, highway='shortcut') # 가는 길
    G_proj.add_edge(node_b, node_a, weight=0.01, length=1, highway='shortcut') # 오는 길
    
    print(f"⚡ 비밀 지름길 생성 완료! (시스템은 이 길을 1m라고 인식합니다)")
    # =========================================================

    # 3. 가중치 설계
    for u, v, k, data in G_proj.edges(keys=True, data=True):
        base_len = data['length']
        penalty = 1.0
        
        hw = data.get('highway', '')
        if isinstance(hw, list): hw = hw[0]
        
        if hw in ['primary', 'trunk']: penalty = 1.5
        # 지름길은 페널티 0 (최우선)
        if hw == 'shortcut': penalty = 0.0
        
        data['custom_cost'] = base_len * penalty

    # 4. 경로 탐색
    # 아까 에러난 이유: nearest_nodes를 G(위경도)에서 찾아야 안전함
    orig_node = ox.distance.nearest_nodes(G, start_latlng[1], start_latlng[0])
    dest_node = ox.distance.nearest_nodes(G, end_latlng[1], end_latlng[0])
    
    try:
        route_smart = nx.shortest_path(G_proj, orig_node, dest_node, weight='custom_cost')
    except nx.NetworkXNoPath:
        print("❌ 경로를 찾을 수 없습니다. (여전히 길이 끊겨 있습니다)")
        return

    # 5. 시각화
    print("4. 지도 생성 중...")
    m = folium.Map(location=start_latlng, zoom_start=16)

    # 님의 지름길을 파란 점선으로 표시 (눈에 띄게)
    folium.PolyLine([shortcut_start, shortcut_end], color='blue', dash_array='5, 5', weight=2, popup="비밀 통로").add_to(m)

    # 추천 경로 (초록색)
    coords_smart = [(G_proj.nodes[n]['y'], G_proj.nodes[n]['x']) for n in route_smart]
    folium.PolyLine(coords_smart, color='#00FF00', weight=5, opacity=0.8, tooltip="최적 경로").add_to(m)
    
    folium.Marker(start_latlng, popup="출발", icon=folium.Icon(color='blue')).add_to(m)
    folium.Marker(end_latlng, popup="도착", icon=folium.Icon(color='red')).add_to(m)

    output_file = "walk_nav_v5_shortcut.html"
    m.save(output_file)
    print(f"\n✅ 성공! '{output_file}' 파일을 열어보세요.")
    print("중간에 '파란 점선'을 타고 넘어가는지 확인하세요.")

if __name__ == "__main__":
    run_pedestrian_nav()