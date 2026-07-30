"""Analyze — paste a channel URL, scrape its VODs, run the pipeline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

import ui_common as ui  # noqa: E402
from vcg import pipeline, twitch  # noqa: E402

ui.inject_css()
ui.page_header("Analyze", "Channel URL in → scraped VODs → chat + TwelveLabs → your graph.")

st.html(
    '<div class="tl-chips" style="margin-bottom:14px">'
    '<span class="tl-chip">1 · scrape VODs</span>'
    '<span class="tl-chip">2 · read chat</span>'
    '<span class="tl-chip">3 · cut peak clips</span>'
    '<span class="tl-chip green">4 · TwelveLabs watches</span>'
    '<span class="tl-chip green">5 · saved to Neo4j</span></div>'
)
st.caption(
    "Use your own channel, or one that's given you permission — Twitch's ToS "
    "doesn't allow downloading broadcasts you don't have rights to."
)

if "vod_list" not in st.session_state:
    st.session_state.vod_list = []


def full_run(vod_id: str, title: str, do_clips: bool):
    """Chat analysis (fast), then optionally clips + TwelveLabs verdicts."""
    with st.status("Running the pipeline…", expanded=True) as status:
        st.write("📥 Downloading chat & scoring the timeline…")
        analysis = pipeline.analyze_vod(vod_id, title=title)
        st.session_state.review_vod = f"twitch:{pipeline.downloader.vod_id(vod_id)}"
        n = len(analysis["peaks"])
        st.write(f"⚡ {n} clip-worthy moments · {analysis['summary']['dead_time_pct']}% dead air"
                 + (" · saved to Neo4j" if analysis.get("persisted") else ""))
        if do_clips and n:
            st.write("✂️ Cutting the peak windows (server-side crop)…")
            bar = st.progress(0.0)
            clips = pipeline.clip_moments(
                vod_id, analysis["peaks"],
                progress=lambda i, t, f: bar.progress(i / t, text=f),
            )
            st.write("👁 TwelveLabs is watching each clip…")
            pipeline.enrich_clips(
                vod_id, clips, title=title,
                progress=lambda i, t, f: bar.progress(i / t, text=f"TwelveLabs · {f}"),
            )
            bar.empty()
        status.update(label="Done — open Review & Clips", state="complete")
    st.success("Analysis saved. See **Review & Clips** for the scrubber and player.")


channel = st.text_input(
    "Twitch channel URL or name",
    placeholder="https://www.twitch.tv/your_channel   ·   or just: your_channel",
)

# Resolve as they type so a wrong paste (search page, VOD link) is obvious
# before they wait on a scrape.
if channel:
    try:
        st.caption(f"Channel: **{twitch.channel_from_url(channel)}**")
    except Exception as exc:
        st.warning(str(exc))

count = st.slider("How many recent VODs to list", 3, 24, 8)

if st.button("Scrape VODs", type="primary", disabled=not channel):
    with st.spinner("Scraping the channel's VOD list…"):
        try:
            st.session_state.vod_list = twitch.list_vods_any(channel, limit=count)
            if not st.session_state.vod_list:
                st.warning("No VODs found — past broadcasts may be disabled or expired.")
        except Exception as exc:
            st.error(str(exc))

if st.session_state.vod_list:
    st.html('<div class="tl-kicker">Select a VOD</div>')
    do_clips = st.checkbox(
        "Also cut clips + get TwelveLabs verdicts (slower, needs the footage)",
        value=True,
    )
    for vod in st.session_state.vod_list:
        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 3, 1])
            with c1:
                if vod.get("thumbnail"):
                    st.image(vod["thumbnail"])
            with c2:
                st.markdown(f"**{vod['title'][:80]}**")
                st.caption(f"{ui.format_time(vod.get('duration_s') or 0)} · [{vod['url']}]({vod['url']})")
            with c3:
                if st.button("Analyze", key=f"an_{vod['id']}", type="primary"):
                    full_run(vod["id"], vod["title"], do_clips)

st.divider()
with st.expander("Or paste one VOD URL directly"):
    single = st.text_input("VOD URL or id", key="single_vod",
                           placeholder="https://www.twitch.tv/videos/123456789")
    one_clips = st.checkbox("Clips + TwelveLabs too", value=True, key="one_clips")
    if st.button("Analyze this VOD", disabled=not single):
        full_run(single.strip(), single.strip(), one_clips)
