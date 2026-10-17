"""Entry point: page setup and navigation between the two tools."""
import streamlit as st

from common import FULLSCREEN_CSS

st.set_page_config(page_title="Vessel tools", layout="wide", initial_sidebar_state="expanded")
st.markdown(FULLSCREEN_CSS, unsafe_allow_html=True)

page = st.navigation([
    st.Page("ukc_page.py", title="UKC what-if", icon="🌊", url_path="ukc", default=True),
    st.Page("route_page.py", title="Sail planning", icon="🧭", url_path="sail-planning"),
])
page.run()
