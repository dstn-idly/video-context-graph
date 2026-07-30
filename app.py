"""Streamlit demo UI — this is what you record for the submission video.

    streamlit run app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

from vcg import graph  # noqa: E402
from vcg.agent import build_agent  # noqa: E402

st.set_page_config(page_title="Video Agent Context Graph", page_icon="🎬", layout="wide")
st.title("🎬 Video Agent Context Graph")
st.caption("TwelveLabs understands the video · Neo4j stores the context · Strands + OpenAI reason over it")

with st.sidebar:
    st.header("Graph")
    try:
        st.json(graph.stats())
        videos = graph.run_cypher("MATCH (v:Video) RETURN v.title AS title, v.video_id AS id")
        for v in videos:
            st.write(f"**{v['title']}**")
            st.code(v["id"], language=None)
    except Exception as exc:
        st.error(f"Neo4j not reachable: {exc}")

    st.divider()
    st.caption("Ingest new video from the CLI:")
    st.code("python scripts/ingest.py --url <URL> --title <TITLE>", language="bash")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state:
    try:
        st.session_state.agent = build_agent()
    except Exception as exc:
        st.error(f"Could not build agent: {exc}")
        st.stop()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask about the videos…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Reasoning over the graph…"):
            try:
                result = st.session_state.agent(prompt)
                answer = str(result)
            except Exception as exc:
                answer = f"Error: {exc}"
        st.markdown(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})
