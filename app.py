"""SPIRE / Puffer AI — entry point.

    streamlit run app.py

Puffer (puffer_app.py) is the product frontend: landing → sign in → workspace,
routed through st.query_params. It calls st.set_page_config itself and owns its
own routing, so it is delegated to rather than mounted as an st.navigation page
(either would fight the other for page config and the URL).

The detailed operator pages remain available on their own:

    streamlit run ui_overview.py     # metrics + cross-stream trend
    streamlit run ui_analyze.py      # channel scrape → full pipeline
    streamlit run ui_review.py       # scrubber, clip player, visual scout
    streamlit run ui_search.py       # Marengo semantic search
    streamlit run ui_coach.py        # Strands agent
    streamlit run ui_graph.py        # Neo4j schema + Workspace links
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "puffer_app.py"), run_name="__main__")
