"""Entry point: page setup and navigation between the tools."""
import streamlit as st

from common import FULLSCREEN_CSS, HERE

ASSETS = HERE / "assets"

st.set_page_config(page_title="groundzero", page_icon=str(ASSETS / "favicon.png"), layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(FULLSCREEN_CSS, unsafe_allow_html=True)
# Top-left of the sidebar, above the page links; the circle mark alone when the sidebar is collapsed
st.logo(str(ASSETS / "logo.png"), size="large", icon_image=str(ASSETS / "icon.png"))

page = st.navigation([
    st.Page("ukc_page.py", title="UKC what-if", icon="🌊", url_path="ukc", default=True),
    st.Page("route_page.py", title="Sail planning", icon="🧭", url_path="sail-planning"),
    st.Page("port_page.py", title="Port view", icon="⚓", url_path="port-view"),
])
page.run()
