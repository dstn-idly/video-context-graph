"""Review & Clips — the player, the color-coded scrubber, and the good stuff."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

import ui_common as ui  # noqa: E402
from vcg import config, graph, pipeline  # noqa: E402

ui.inject_css()
ui.page_header("Review & Clips", "Scrub the whole VOD by activity, then watch the moments that matter.")

try:
    overview = graph.performance_overview()
except Exception as exc:
    st.error(f"Neo4j unreachable: {exc}")
    st.stop()

if not overview:
    st.info("Nothing analyzed yet — run a VOD through **Analyze** first.")
    st.stop()

titles = {r["video_id"]: (r.get("title") or r["video_id"]) for r in overview}
ids = [r["video_id"] for r in reversed(overview)]
default = st.session_state.get("review_vod")
index = ids.index(default) if default in ids else 0
video_id = st.selectbox("Stream", ids, index=index, format_func=lambda v: titles.get(v, v))

row = next(r for r in overview if r["video_id"] == video_id)
url = row.get("url") or ""
duration = int(row.get("duration_s") or 0)
vod_number = video_id.split(":", 1)[-1]

ui.metric_row([
    ("Length", ui.format_time(duration), ""),
    ("Chat velocity", f"{row.get('msgs_per_min') or 0:.0f}", "/min"),
    ("Dead air", f"{row.get('dead_pct') or 0:.1f}%", ""),
    ("Moments", f"{int(row.get('peak_count') or 0)}", ""),
])

moments = graph.video_moments(video_id)
dead = graph.video_dead_spots(video_id)

st.html('<div class="tl-kicker">Activity scrubber — click any marker to open Twitch there</div>')
ui.scrubber(duration, moments, dead, url)

# ---------------------------------------------------------------- clips
st.html('<div class="tl-kicker">Clip collection</div>')

clip_dir = config.ROOT / "clips" / vod_number
local_files = sorted(clip_dir.glob("*.mp4")) if clip_dir.is_dir() else []


def file_for(start: int) -> Path | None:
    for f in local_files:
        if f.name.endswith(f"_{start}s.mp4") or f.name == f"scout_{start}s.mp4":
            return f
    return None


chat_moments = [m for m in moments if m.get("detector") != "twelvelabs"]
visual_moments = [m for m in moments if m.get("detector") == "twelvelabs"]

if not moments:
    st.caption("No moments recorded for this stream yet.")

tabs = st.tabs([f"💬 Chat-detected ({len(chat_moments)})",
                f"👁 TwelveLabs-scouted ({len(visual_moments)})"])

for tab, group in zip(tabs, [chat_moments, visual_moments]):
    with tab:
        if not group:
            st.caption("None yet." if group is chat_moments else
                       "Run the scout below — TwelveLabs watches footage chat ignored.")
        cols = st.columns(2)
        for i, m in enumerate(sorted(group, key=lambda x: -(x.get("score") or 0))):
            with cols[i % 2], st.container(border=True):
                kind = m.get("kind") or "action"
                start = int(m.get("start") or 0)
                st.markdown(
                    f'<span class="tl-chip green">{kind.upper()}</span> '
                    f'<span class="tl-chip">score {m.get("score") or 0:.0f}</span> '
                    f'<span class="tl-chip">{ui.format_time(start)}</span>',
                    unsafe_allow_html=True,
                )
                local = file_for(start)
                if local:
                    st.video(str(local))
                if m.get("reason"):
                    st.markdown(f'<div class="clip-meta">{m["reason"][:110]}</div>',
                                unsafe_allow_html=True)
                if m.get("ai_verdict"):
                    st.markdown(
                        f'<div class="verdict"><b>WHAT TWELVELABS SAW</b><br>'
                        f'{str(m["ai_verdict"]).strip()[:220]}</div>',
                        unsafe_allow_html=True,
                    )
                if url:
                    st.markdown(f"[▶ Watch on Twitch]({ui.twitch_ts(url, start)})")

# ---------------------------------------------------------------- visual scout
st.html('<div class="tl-kicker">TwelveLabs visual scout</div>')
with st.container(border=True):
    st.markdown(
        "Chat only reacts to what viewers noticed. The scout samples windows "
        "across the **whole VOD**, has TwelveLabs watch each one, and keeps "
        "anything remarkable — completely independent of chat."
    )
    n = st.slider("Windows to watch", 3, 10, 4,
                  help="Each window is ~40s of footage: download → index → Pegasus verdict.")
    if st.button("👁 Scout this VOD", type="primary"):
        bar = st.progress(0.0, text="Starting…")
        try:
            finds = pipeline.visual_scout(
                vod_number, samples=n,
                progress=lambda i, t, f: bar.progress(i / t, text=f),
            )
            bar.empty()
            kept = [f for f in finds if f["rating"] >= 6]
            st.success(f"Watched {len(finds)} windows — {len(kept)} worth keeping.")
            for f in finds:
                icon = "🎯" if f["rating"] >= 6 else "·"
                st.markdown(f"{icon} **{ui.format_time(f['start'])}** — rated "
                            f"**{f['rating']}/10**")
                st.caption(f["verdict"][:250])
            if kept:
                st.rerun()
        except Exception as exc:
            bar.empty()
            st.error(str(exc))
