"""Overview — the SaaS home: metrics, trend, your streams."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import ui_common as ui  # noqa: E402
from vcg import graph  # noqa: E402

ui.inject_css()

ui.banner(
    "🟢 <b>SPIRE</b> — paste your channel, and know exactly which moments "
    "carried your stream. Powered by TwelveLabs, Neo4j, OpenAI & Strands on AWS."
)
ui.page_header("Overview", "Your channel's performance, one stream at a time.")

try:
    overview = graph.performance_overview()
    moments = graph.top_moments(6)
    online = True
except Exception as exc:
    overview, moments, online = [], [], False
    st.error(f"Neo4j unreachable: {exc}")

if not overview:
    if online:
        st.info("No streams analyzed yet — head to **Analyze** and paste your channel URL.")
else:
    hours = sum((r.get("duration_s") or 0) for r in overview) / 3600
    avg_mpm = sum((r.get("msgs_per_min") or 0) for r in overview) / len(overview)
    avg_dead = sum((r.get("dead_pct") or 0) for r in overview) / len(overview)
    clip_ready = sum(int(r.get("peak_count") or 0) for r in overview)
    verified = sum(1 for m in moments if m.get("ai_verdict"))

    ui.metric_row([
        ("Streams analyzed", f"{len(overview)}", ""),
        ("Hours profiled", f"{hours:.1f}", ""),
        ("Avg chat velocity", f"{avg_mpm:.0f}", "/min"),
        ("Avg dead air", f"{avg_dead:.1f}%", ""),
        ("Clip-ready moments", f"{clip_ready}", f"{verified} verified"),
    ])

    st.html('<div class="tl-kicker">Performance across streams</div>')
    frame = pd.DataFrame([
        {
            "stream": (r.get("title") or r["video_id"])[:24],
            "order": i,
            "chat velocity": r.get("msgs_per_min") or 0,
            "dead air %": r.get("dead_pct") or 0,
        }
        for i, r in enumerate(overview)
    ])
    bars = alt.Chart(frame).mark_bar(color="#10B981", cornerRadiusEnd=4, size=42).encode(
        x=alt.X("stream:N", sort=alt.EncodingSortField("order"), title=None,
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("chat velocity:Q", title="msgs/min"),
        tooltip=["stream", "chat velocity", "dead air %"],
    )
    line = alt.Chart(frame).mark_line(
        color="#EF4444", point=alt.OverlayMarkDef(color="#EF4444", size=70)
    ).encode(
        x=alt.X("stream:N", sort=alt.EncodingSortField("order")),
        y=alt.Y("dead air %:Q", title="dead air %"),
        tooltip=["stream", "dead air %"],
    )
    st.altair_chart(
        alt.layer(bars, line).resolve_scale(y="independent").properties(height=260),
        use_container_width=True,
    )

    st.html('<div class="tl-kicker">My streams</div>')
    for row in reversed(overview):
        with st.container(border=True):
            left, mid, right = st.columns([3, 2, 1])
            with left:
                st.markdown(f"**{row.get('title') or row['video_id']}**")
                st.caption(
                    f"{ui.format_time(row.get('duration_s') or 0)} · "
                    f"{int(row.get('total_messages') or 0):,} messages"
                )
            with mid:
                st.caption(
                    f"⚡ {row.get('msgs_per_min') or 0:.0f} msgs/min · "
                    f"💤 {row.get('dead_pct') or 0:.1f}% dead · "
                    f"🎬 {int(row.get('peak_count') or 0)} moments"
                )
            with right:
                if st.button("Review →", key=f"rev_{row['video_id']}"):
                    st.session_state.review_vod = row["video_id"]
                    st.switch_page("ui_review.py")

    if moments:
        st.html('<div class="tl-kicker">Best moments, all streams</div>')
        cols = st.columns(3)
        for i, m in enumerate(moments[:6]):
            with cols[i % 3], st.container(border=True):
                kind = m.get("kind") or "action"
                det = "👁 TwelveLabs" if m.get("detector") == "twelvelabs" else "💬 chat"
                st.markdown(
                    f'<span class="tl-chip green">{kind.upper()}</span> '
                    f'<span class="tl-chip">{det}</span> '
                    f'<span class="tl-chip">score {m.get("score") or 0:.0f}</span>',
                    unsafe_allow_html=True,
                )
                st.caption(f"{(m.get('title') or '')[:40]} · {ui.format_time(m.get('start') or 0)}")
                if m.get("ai_verdict"):
                    st.markdown(
                        f'<div class="verdict"><b>WHAT TWELVELABS SAW</b><br>'
                        f'{str(m["ai_verdict"]).strip()[:160]}</div>',
                        unsafe_allow_html=True,
                    )
                if m.get("url"):
                    st.markdown(f"[Watch on Twitch]({ui.twitch_ts(m['url'], int(m['start']))})")
