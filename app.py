"""Streamlit front end — browse VODs, analyze them, clip the best moments.

    streamlit run app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from vcg import graph, highlights, pipeline, twitch  # noqa: E402

st.set_page_config(page_title="Stream Autopsy", page_icon="🎬", layout="wide")

KIND_COLORS = {
    "funny": "#f5a623",
    "hype": "#7ed321",
    "awkward": "#d0021b",
    "tense": "#bd10e0",
    "action": "#4a90d9",
}


def state(key, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


state("vods", [])
state("analysis", None)
state("clips", [])

st.title("🎬 Stream Autopsy")
st.caption(
    "Chat finds the moments · TwelveLabs confirms them · Neo4j remembers · Strands answers"
)

browse, timeline, clips_tab, ask = st.tabs(
    ["1 · Browse VODs", "2 · Timeline", "3 · Clips", "4 · Ask"]
)

# ---------------------------------------------------------------- browse
def run_analysis(target: str, title: str):
    """Analyze one VOD and stash it in session state."""
    with st.spinner("Downloading chat and scoring the timeline…"):
        try:
            st.session_state.analysis = pipeline.analyze_vod(target)
            st.session_state.analysis["title"] = title
            st.session_state.clips = []
            st.success("Done — open the Timeline tab.")
        except Exception as exc:
            st.error(str(exc))


with browse:
    # Direct entry first: it's the fastest path in a demo, and it works offline
    # against cached chat, so it should never be buried behind an expander.
    direct, go = st.columns([3, 1])
    with direct:
        manual = st.text_input(
            "VOD URL or id",
            key="manual_url",
            placeholder="https://www.twitch.tv/videos/123456789   (or 999000111 for the demo)",
        )
    with go:
        st.write("")
        if st.button("Analyze", key="go_manual", type="primary"):
            if manual.strip():
                run_analysis(manual.strip(), manual.strip())
            else:
                st.warning("Paste a VOD URL or id first.")

    st.divider()
    st.markdown("#### …or browse a channel")

    left, right = st.columns([2, 1])
    with left:
        channel = st.text_input("Twitch channel", placeholder="your_channel_name")
    with right:
        how_many = st.number_input("How many VODs", 1, 100, 20)

    st.caption(
        "Use your own channel, or one that's given you permission — "
        "Twitch's ToS doesn't allow downloading broadcasts you don't have rights to."
    )

    if st.button("Fetch VODs", type="primary", disabled=not channel):
        try:
            user = twitch.get_user(channel.strip())
            vods = twitch.list_vods(user["id"], limit=int(how_many))
            st.session_state.vods = vods
            st.success(f"{user['display_name']} — {len(vods)} VODs")
        except Exception as exc:
            st.error(str(exc))

    if st.session_state.vods:
        st.divider()
        st.subheader("Pick one to analyze")
        st.caption("Analysis reads chat only — no video downloads at this step, so it's quick.")

        for vod in st.session_state.vods:
            seconds = twitch.parse_duration(vod.get("duration", ""))
            cols = st.columns([1, 4, 1])
            thumb = (vod.get("thumbnail_url") or "").replace("%{width}", "160").replace(
                "%{height}", "90"
            )
            with cols[0]:
                if thumb and "{" not in thumb:
                    st.image(thumb)
            with cols[1]:
                st.markdown(f"**{vod['title'][:90]}**")
                st.caption(
                    f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m · "
                    f"{vod.get('view_count', 0):,} views · {vod['created_at'][:10]}"
                )
            with cols[2]:
                if st.button("Analyze", key=f"a_{vod['id']}"):
                    run_analysis(vod["id"], vod["title"])

# ---------------------------------------------------------------- timeline
with timeline:
    analysis = st.session_state.analysis
    if not analysis:
        st.info("Analyze a VOD in the Browse tab first.")
    else:
        summary = analysis["summary"]
        st.subheader(analysis.get("title", analysis["vod_id"]))

        a, b, c, d = st.columns(4)
        a.metric("Length", f"{summary['duration_seconds'] // 3600}h"
                           f"{(summary['duration_seconds'] % 3600) // 60:02d}m")
        b.metric("Chat", f"{summary['total_messages']:,}",
                 f"{summary['messages_per_minute']}/min")
        c.metric("Clippable moments", summary["peak_moments"])
        d.metric("Dead air", f"{summary['dead_time_pct']}%",
                 f"{summary['dead_time_seconds'] // 60}m", delta_color="inverse")

        rows = pipeline.timeline_rows(analysis["buckets"])
        frame = pd.DataFrame(rows)

        base = alt.Chart(frame).mark_area(
            line={"color": "#4a90d9"},
            color=alt.Gradient(
                gradient="linear",
                stops=[alt.GradientStop(color="#4a90d9", offset=0),
                       alt.GradientStop(color="rgba(74,144,217,0.05)", offset=1)],
                x1=1, x2=1, y1=1, y2=0,
            ),
        ).encode(
            x=alt.X("minute:Q", title="Minutes into stream"),
            y=alt.Y("heat:Q", title="Engagement"),
            tooltip=["minute", "messages", "chatters", "heat"],
        )

        layers = [base]

        if analysis["dead_spots"]:
            dead_frame = pd.DataFrame(
                [{"start": s.start / 60, "end": s.end / 60} for s in analysis["dead_spots"]]
            )
            layers.append(
                alt.Chart(dead_frame).mark_rect(color="#d0021b", opacity=0.13).encode(
                    x="start:Q", x2="end:Q"
                )
            )

        if analysis["peaks"]:
            peak_frame = pd.DataFrame([
                {"minute": m.start / 60, "heat": 100, "kind": m.kind, "score": m.score}
                for m in analysis["peaks"]
            ])
            layers.append(
                alt.Chart(peak_frame).mark_point(size=140, filled=True, opacity=0.9).encode(
                    x="minute:Q", y="heat:Q",
                    color=alt.Color("kind:N",
                                    scale=alt.Scale(domain=list(KIND_COLORS),
                                                    range=list(KIND_COLORS.values())),
                                    legend=alt.Legend(title="Moment")),
                    tooltip=["kind", "score", "minute"],
                )
            )

        st.altair_chart(alt.layer(*layers).properties(height=280), use_container_width=True)
        st.caption("Blue = engagement. Red bands = dead air. Dots = clippable moments.")

        moments, deadcol = st.columns(2)
        with moments:
            st.markdown("### 🔥 Worth clipping")
            for m in analysis["peaks"]:
                mins = f"{m.start // 60}:{m.start % 60:02d}"
                with st.expander(f"`{mins}` · {m.kind.upper()} · {m.score}"):
                    st.write(m.reason)
                    st.caption(f"{m.messages} msgs from {m.chatters} chatters")
                    st.markdown(f"[Watch on Twitch]({analysis['url']}?t="
                                f"{m.start // 3600}h{(m.start % 3600) // 60}m{m.start % 60}s)")
                    for line in m.sample:
                        st.text(f"  {line}")

        with deadcol:
            st.markdown("### 💤 Tighten these up")
            if not analysis["dead_spots"]:
                st.success("No sustained dead air. Pacing held up.")
            for s in analysis["dead_spots"]:
                mins = f"{s.start // 60}:{s.start % 60:02d}"
                with st.expander(f"`{mins}` · {(s.end - s.start) // 60}m quiet · {s.severity}"):
                    st.write(s.note)
                    st.markdown(f"[Watch on Twitch]({analysis['url']}?t="
                                f"{s.start // 3600}h{(s.start % 3600) // 60}m{s.start % 60}s)")

# ---------------------------------------------------------------- clips
with clips_tab:
    analysis = st.session_state.analysis
    if not analysis:
        st.info("Analyze a VOD first.")
    elif not analysis["peaks"]:
        st.warning("No moments crossed the threshold for this VOD.")
    else:
        st.caption(
            f"Downloads only the {len(analysis['peaks'])} peak windows using "
            "TwitchDownloader's server-side crop — a few minutes of video, not the whole VOD."
        )
        col1, col2 = st.columns([1, 1])
        quality = col1.selectbox("Quality", ["480p30", "720p60", "1080p60"], index=0)
        use_ai = col2.checkbox("Also analyze with TwelveLabs", value=False,
                               help="Confirms what actually happened on screen and writes it to the graph.")

        if st.button("Cut the clips", type="primary"):
            bar = st.progress(0.0, text="Starting…")

            def report(i, total, name):
                bar.progress(i / total, text=f"{i}/{total} — {name}")

            try:
                results = pipeline.clip_moments(
                    analysis["vod_id"], analysis["peaks"],
                    quality=quality, progress=report,
                )
                if use_ai:
                    bar.progress(0.0, text="Sending to TwelveLabs…")
                    results = pipeline.enrich_clips(
                        analysis["vod_id"], results,
                        title=analysis.get("title", ""), progress=report,
                    )
                st.session_state.clips = results
                bar.empty()
            except Exception as exc:
                bar.empty()
                st.error(str(exc))

        for clip in st.session_state.clips:
            mins = f"{clip['start'] // 60}:{clip['start'] % 60:02d}"
            st.markdown(f"#### `{mins}` · {clip['kind'].upper()} · score {clip['score']}")
            if clip.get("error"):
                st.error(clip["error"])
            elif clip.get("path") and Path(clip["path"]).exists():
                video_col, info_col = st.columns([1, 1])
                with video_col:
                    st.video(clip["path"])
                with info_col:
                    st.write(clip["reason"])
                    if clip.get("ai_verdict"):
                        st.info(clip["ai_verdict"])
                    with open(clip["path"], "rb") as fh:
                        st.download_button("Download", fh.read(),
                                           file_name=Path(clip["path"]).name,
                                           key=f"d_{clip['start']}")
            st.divider()

# ---------------------------------------------------------------- ask
with ask:
    st.caption("Ask the Strands agent about anything already in the graph.")
    try:
        st.json(graph.stats())
    except Exception as exc:
        st.error(f"Neo4j not reachable: {exc}")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about the videos…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    if "agent" not in st.session_state:
                        from vcg.agent import build_agent
                        st.session_state.agent = build_agent()
                    answer = str(st.session_state.agent(prompt))
                except Exception as exc:
                    answer = f"Error: {exc}"
            st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
