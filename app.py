"""SPIRE — stream performance intelligence for Twitch streamers.

    streamlit run app.py

Flow: paste your channel URL → SPIRE scrapes your VODs → downloads chat +
footage → TwelveLabs watches, Neo4j remembers → a Strands agent coaches you.
"""
import streamlit as st

st.set_page_config(
    page_title="SPIRE · Stream Performance",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
)

nav = st.navigation(
    [
        st.Page("ui_overview.py", title="Overview", icon=":material/home:", default=True),
        st.Page("ui_analyze.py", title="Analyze", icon=":material/query_stats:"),
        st.Page("ui_review.py", title="Review & Clips", icon=":material/movie:"),
        st.Page("ui_search.py", title="Search", icon=":material/search:"),
        st.Page("ui_coach.py", title="Coach", icon=":material/forum:"),
        st.Page("ui_graph.py", title="Graph", icon=":material/hub:"),
    ]
)

with st.sidebar:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;padding:2px 0 10px">'
        '<div style="width:30px;height:30px;border-radius:8px;background:#087443;'
        'display:grid;place-items:center;color:#fff;font-weight:900">◢</div>'
        '<div style="font-weight:800;letter-spacing:.1em;font-size:14px">SPIRE'
        '<div style="font-size:8.5px;color:#6B7280;letter-spacing:.14em">STREAM PERFORMANCE</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )

nav.run()
