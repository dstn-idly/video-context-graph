"""SPIRE — entry point.

    streamlit run app.py

Two pages: the SPIRE dashboard (Barrat's presentation layer over the live
Neo4j graph) and Stream Autopsy (VOD selection, chat-driven timeline, clip
cutting, TwelveLabs analysis).
"""
import streamlit as st

st.set_page_config(
    page_title="SPIRE · Video Context Graph",
    page_icon="◢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

nav = st.navigation(
    [
        st.Page("ui_spire.py", title="SPIRE", icon=":material/hub:", default=True),
        st.Page("ui_autopsy.py", title="Stream Autopsy", icon=":material/movie:"),
    ],
    position="top",
)
nav.run()
