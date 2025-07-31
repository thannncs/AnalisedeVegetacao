import streamlit as st
import ee
import json
import folium
from streamlit_folium import st_folium
import pandas as pd
from folium.plugins import Draw
from geopy.geocoders import Nominatim

def inicializar_ee():
    try:
        if "earthengine" in st.secrets:
            credentials_dict = st.secrets["earthengine"]
            credentials = ee.ServiceAccountCredentials(
                credentials_dict["client_email"],
                key_data=json.dumps(credentials_dict)
            )
            ee.Initialize(credentials)
        else:
            
            credentials = ee.ServiceAccountCredentials(
                'sua-conta@estagio-461414.iam.gserviceaccount.com',
                'credentials.json'
            )
            ee.Initialize(credentials)
    except Exception as e:
        st.error(f"Erro ao inicializar Earth Engine: {e}")

# Inicializa Earth Engine
inicializar_ee()

if 'drawings' not in st.session_state:
    st.session_state.drawings = []

st.set_page_config(layout="wide")
st.title("Análise de Vegetação com NDVI (Sentinel-2)")

# Campo de busca de local
query = st.sidebar.text_input("Pesquisar local")

# Botão para limpar desenhos
if st.sidebar.button("Limpar desenhos"):
    st.session_state.drawings = []
    st.experimental_rerun()

lat, lon = -14.2, -51.9  # Brasil

if query:
    geolocator = Nominatim(user_agent="my_streamlit_app")
    locations = geolocator.geocode(query, exactly_one=False, limit=5)
    
    if locations:
        options = [loc.address for loc in locations]
        choice = st.sidebar.selectbox("Escolha uma opção:", options)
        if choice:
            selected = locations[options.index(choice)]
            lat, lon = selected.latitude, selected.longitude
    else:
        st.sidebar.warning("Nenhum resultado encontrado para a pesquisa.")

# Datas
start_date = st.sidebar.date_input("Data Inicial", value=pd.to_datetime("2022-01-01"))
end_date = st.sidebar.date_input("Data Final", value=pd.to_datetime("2022-01-31"))

# Slider para filtro nuvens
cloud_limit = st.sidebar.slider(
    "Máximo percentual de nuvens permitido (%)",
    min_value=0, max_value=100, value=30, step=5
)

# Slider para threshold mínimo do NDVI
ndvi_threshold = st.sidebar.slider(
    "Threshold mínimo para NDVI",
    min_value=0.0,
    max_value=1.0,
    value=0.3,
    step=0.01,
    help="Defina o valor mínimo de NDVI para filtrar áreas vegetadas"
)

zoom = 10  # Zoom para local selecionado

# Criar mapa Folium sem camada base padrão (tiles=None)
m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None)

# Região de interesse
roi = ee.Geometry.Point([lon, lat]).buffer(1000000)

# Coleção Sentinel-2 filtrada
collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterDate(str(start_date), str(end_date)) \
    .filterBounds(roi) \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_limit))

count = collection.size().getInfo()
if count == 0:
    st.warning(f"Nenhuma imagem Sentinel-2 encontrada para o filtro de nuvens ≤ {cloud_limit}%. Tente aumentar o limite ou mudar as datas.")
    st.stop()
else:
    sentinel = collection.median().divide(10000).clip(roi)

# Visualização RGB Sentinel-2
rgb_vis_params = {
    'bands': ['B4', 'B3', 'B2'],
    'min': 0,
    'max': 0.3
}

mapid = sentinel.getMapId(rgb_vis_params)
tiles_url = mapid['tile_fetcher'].url_format if 'tile_fetcher' in mapid else None

folium.TileLayer(
    tiles=tiles_url,
    attr='Sentinel-2 via Google Earth Engine',
    name='Sentinel-2 RGB',
    overlay=False,
    control=True,
    show=True
).add_to(m)

# Ferramenta de desenho 
draw = Draw(
    position='topleft',
    draw_options={'polyline': False,
                  'circle': False,
                  'circlemarker': False,
                  'marker': False,
                  'rectangle': True,
                  'polygon': True},
    edit_options={'edit': True}
)
draw.add_to(m)

folium.LayerControl().add_to(m)

# Mostrar mapa e capturar desenhos
output = st_folium(
    m,
    height=600,
    width=1000,
    returned_objects=["all_drawings", "last_active_drawing"],
    key="map"
)

if output and "all_drawings" in output and output["all_drawings"] is not None:
    st.session_state.drawings = output["all_drawings"]

if st.session_state.drawings:
    last_drawing = st.session_state.drawings[-1]
    
    if "geometry" in last_drawing:
        geom_json = last_drawing["geometry"]
        st.success("Área selecionada!")

        try:
            ee_geom = ee.Geometry(geom_json)

            sentinel_clip = sentinel.clip(ee_geom)
            ndvi = sentinel_clip.normalizedDifference(['B8', 'B4']).rename('NDVI')

            # Aplica o filtro de threshold no NDVI
            ndvi_masked = ndvi.updateMask(ndvi.gte(ndvi_threshold))

            ndvi_vis = {'min': ndvi_threshold, 'max': 1, 'palette': ['blue', 'white', 'green']}
            ndvi_mapid = ndvi_masked.getMapId(ndvi_vis)
            ndvi_tiles_url = ndvi_mapid['tile_fetcher'].url_format if 'tile_fetcher' in ndvi_mapid else None

            # Cria um mapa focado na área recortada
            bounds = ee_geom.bounds().getInfo()['coordinates'][0]
            m2 = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None)
            
            # Adiciona a camada NDVI filtrada
            folium.TileLayer(
                tiles=ndvi_tiles_url,
                attr='NDVI via Google Earth Engine',
                name='NDVI',
                overlay=False,
                control=True,
                show=True
            ).add_to(m2)
            
            # Ajusta a visualização para a área recortada
            m2.fit_bounds([
                [bounds[0][1], bounds[0][0]],  
                [bounds[2][1], bounds[2][0]]   
            ])
            
            # Adiciona o polígono da área selecionada
            folium.GeoJson(
                geom_json,
                style_function=lambda x: {
                    'fillColor': 'none',
                    'color': 'red',
                    'weight': 2,
                    'fillOpacity': 0
                }
            ).add_to(m2)
            
            # Exibe o mapa com NDVI filtrado
            st_folium(m2, height=600, width=1000, key="ndvi_map")

            # Calcula a área total em metros quadrados
            area = ee_geom.area()
            
            # Cria uma imagem binária onde 1 = vegetação (NDVI >= threshold)
            vegetation_mask = ndvi.gte(ndvi_threshold)
            
            # Conta o total de pixels válidos (não nulos) na imagem NDVI
            total_pixels = ndvi.reduceRegion(
                reducer=ee.Reducer.count(),
                geometry=ee_geom,
                scale=10,
                maxPixels=1e9
            ).getInfo().get('NDVI', 1)  
            
            # Conta os pixels que são vegetação (NDVI >= threshold)
            vegetation_pixels = vegetation_mask.reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=ee_geom,
                scale=10,
                maxPixels=1e9
            ).getInfo().get('NDVI', 0)  
            
            # Calcula a porcentagem de vegetação
            if total_pixels > 0:
                vegetation_percentage = (vegetation_pixels / total_pixels) * 100
            else:
                vegetation_percentage = 0
            
            # Obtém as estatísticas do NDVI
            ndvi_stats = ndvi_masked.reduceRegion(
                reducer=ee.Reducer.minMax().combine(
                    reducer2=ee.Reducer.mean(),
                    sharedInputs=True
                ),
                geometry=ee_geom,
                scale=10,
                maxPixels=1e9
            ).getInfo()

            # Exibe as estatísticas
            st.write("### Análise de Vegetação")
            st.metric("Porcentagem de Vegetação", f"{vegetation_percentage:.2f}%")
            
            st.write("### Estatísticas NDVI (apenas vegetação)")
            col1, col2 = st.columns(2)
            
            with col1:
                st.metric("Mínimo", f"{ndvi_stats.get('NDVI_min', 0):.3f}")
                st.metric("Máximo", f"{ndvi_stats.get('NDVI_max', 0):.3f}")
            
            with col2:
                st.metric("Média", f"{ndvi_stats.get('NDVI_mean', 0):.3f}")
                st.metric("Threshold Aplicado", f"{ndvi_threshold:.2f}")

        except Exception as e:
            st.error(f"Erro ao processar geometria: {e}")
else:
    st.info("Desenhe uma região no mapa para analisar.")
