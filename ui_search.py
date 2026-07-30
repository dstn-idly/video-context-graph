"""Search — TwelveLabs semantic search over everything you've indexed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

import ui_common as ui  # noqa: E402
from vcg import clients, config, graph  # noqa: E402

ui.inject_css()
ui.page_header("Search", "Describe a moment in plain words — Marengo finds the exact seconds.")

st.html(
    '<div class="tl-chips" style="margin-bottom:14px">'
    '<span class="tl-chip green">SEMANTIC</span>'
    '<span class="tl-chip">VISUAL + AUDIO</span>'
    '<span class="tl-chip">ALL YOUR INDEXED FOOTAGE</span></div>'
)

query = st.text_input("Search your footage", placeholder="someone gets pushed off a bus…")

if query:
    try:
        with st.spinner("Marengo is searching…"):
            hits = clients.search(config.require("TWELVELABS_INDEX_ID"), query, limit=8)
        if not hits:
            st.info("No matching footage.")
        for hit in hits:
            linked = graph.run_cypher(
                """
                MATCH (v:Video)-[:HAS_SCENE|HAS_MOMENT]->(x)
                WHERE x.tl_video_id = $tl
                RETURN v.video_id AS vid, v.title AS title, v.source_url AS url,
                       x.start AS clip_start LIMIT 1
                """,
                {"tl": hit["video_id"]},
            )
            with st.container(border=True):
                if linked:
                    info = linked[0]
                    absolute = int(info.get("clip_start") or 0) + int(hit["start"])
                    left, right = st.columns([3, 1])
                    with left:
                        st.markdown(f"**{(info.get('title') or info['vid'])[:60]}**")
                        st.caption(
                            f"rank {hit['rank']} · at {ui.format_time(absolute)} in the VOD "
                            f"(clip {ui.format_time(hit['start'])}–{ui.format_time(hit['end'])})"
                        )
                        if info.get("url"):
                            st.markdown(f"[▶ Watch on Twitch]({ui.twitch_ts(info['url'], absolute)})")
                    with right:
                        vod_number = str(info["vid"]).split(":", 1)[-1]
                        clip_dir = config.ROOT / "clips" / vod_number
                        start = int(info.get("clip_start") or 0)
                        for f in (sorted(clip_dir.glob("*.mp4")) if clip_dir.is_dir() else []):
                            if f.name.endswith(f"_{start}s.mp4") or f.name == f"scout_{start}s.mp4":
                                st.video(str(f), start_time=int(hit["start"]))
                                break
                else:
                    st.markdown(f"**Indexed clip** `{str(hit['video_id'])[:14]}…`")
                    st.caption(f"rank {hit['rank']} · {ui.format_time(hit['start'])}–{ui.format_time(hit['end'])}")
    except Exception as exc:
        st.error(f"Search unavailable: {exc}")
else:
    st.caption("Try: “helicopter”, “someone yelling”, “a crowd of people”, “rage quit”…")
