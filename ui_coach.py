"""Coach — the Strands agent gives feedback grounded in your stream history."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

import ui_common as ui  # noqa: E402

ui.inject_css()
ui.page_header("Coach", "A Strands agent that reasons over your Neo4j history and TwelveLabs verdicts.")

QUICK = [
    "Give me honest feedback on my latest stream — what worked, what didn't?",
    "Which stream had the least dead air, and why might that be?",
    "What's my most viral-ready moment? Quote what TwelveLabs saw.",
    "Am I improving stream over stream? Use the numbers.",
]

cols = st.columns(2)
for i, q in enumerate(QUICK):
    if cols[i % 2].button(q, key=f"quick_{i}", use_container_width=True):
        st.session_state.coach_pending = q

if "coach_messages" not in st.session_state:
    st.session_state.coach_messages = []

for message in st.session_state.coach_messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Ask your stream history anything…")
if not prompt and st.session_state.get("coach_pending"):
    prompt = st.session_state.pop("coach_pending")

if prompt:
    st.session_state.coach_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Traversing your graph…"):
            try:
                if "coach_agent" not in st.session_state:
                    from vcg.agent import build_agent
                    st.session_state.coach_agent = build_agent()
                answer = str(st.session_state.coach_agent(prompt))
            except Exception as exc:
                answer = f"The agent is unavailable: {exc}"
        st.markdown(answer)
    st.session_state.coach_messages.append({"role": "assistant", "content": answer})
