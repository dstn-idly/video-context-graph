"""Shared design system — light, card-based, in the spirit of the
TwelveLabs playground: white surfaces, soft borders, rounded corners,
green accents, small uppercase chips."""

from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

GREEN = "#087443"
KIND_COLORS = {
    "funny": "#F59E0B",
    "hype": "#10B981",
    "awkward": "#EF4444",
    "tense": "#8B5CF6",
    "action": "#3B82F6",
    "visual": "#0EA5E9",
}
DEAD = "#FCA5A5"


def inject_css() -> None:
    st.html(
        """
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
          html, body, [class*="css"] { font-family: "Inter", ui-sans-serif, system-ui; }
          .block-container { max-width: 1240px; padding-top: 1.4rem; }

          [data-testid="stSidebar"] {
            background: #FAFAF8; border-right: 1px solid #ECECEA;
          }
          [data-testid="stSidebarNav"] a { border-radius: 10px; }

          .tl-banner {
            display: flex; align-items: center; gap: 10px;
            padding: 12px 18px; margin-bottom: 18px;
            border-radius: 12px; font-size: 13.5px; color: #065F46;
            background: linear-gradient(90deg, #ECFDF5, #D1FAE5 45%, #FEF9C3);
            border: 1px solid #A7F3D0;
          }
          .tl-banner b { font-weight: 700; }

          .tl-h1 { font-size: 26px; font-weight: 800; letter-spacing: -.02em;
                   color: #111827; margin: 2px 0 4px; }
          .tl-sub { color: #6B7280; font-size: 13.5px; margin-bottom: 16px; }

          .tl-card {
            padding: 16px 18px; border: 1px solid #E7E5E4; border-radius: 16px;
            background: #FFFFFF; box-shadow: 0 1px 2px rgba(16,24,40,.04);
          }
          .tl-chips { display: flex; gap: 8px; flex-wrap: wrap; }
          .tl-chip {
            padding: 4px 10px; border: 1px solid #E5E7EB; border-radius: 999px;
            background: #F9FAFB; color: #374151; font-size: 11.5px; font-weight: 600;
          }
          .tl-chip.green { background: #ECFDF5; border-color: #A7F3D0; color: #065F46; }

          .tl-metric label { color: #6B7280; font-size: 10.5px; font-weight: 700;
                             letter-spacing: .08em; text-transform: uppercase; }
          .tl-metric strong { display: block; font-size: 24px; font-weight: 800;
                              color: #111827; margin-top: 2px; }
          .tl-metric small { color: #6B7280; font-size: 11px; font-weight: 600; }

          .tl-kicker { color: #6B7280; font-size: 11px; font-weight: 700;
                       letter-spacing: .09em; text-transform: uppercase; margin: 18px 0 8px; }

          .scrub-wrap { position: relative; height: 26px; margin: 6px 0 2px; }
          .scrub-track {
            position: absolute; inset: 6px 0; border-radius: 999px;
            background: #E5E7EB; overflow: hidden;
          }
          .scrub-seg { position: absolute; top: 0; bottom: 0; display: block; }
          .scrub-marker {
            position: absolute; top: 0; bottom: 0; display: block;
            min-width: 5px; border-radius: 2px; opacity: .95;
          }
          .scrub-marker:hover { outline: 2px solid #111827; z-index: 3; }
          .scrub-times { display: flex; justify-content: space-between;
                         color: #9CA3AF; font-size: 10.5px; font-weight: 600; }
          .scrub-legend { display: flex; gap: 14px; margin-top: 6px; flex-wrap: wrap; }
          .scrub-legend span { display: inline-flex; align-items: center; gap: 6px;
                               color: #6B7280; font-size: 11px; font-weight: 600; }
          .scrub-legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

          .clip-meta { color: #6B7280; font-size: 11.5px; margin-top: 4px; }
          .verdict {
            margin-top: 8px; padding: 9px 11px; border-radius: 10px;
            background: #F0FDF4; border: 1px solid #BBF7D0;
            color: #14532D; font-size: 12px; line-height: 1.55;
          }
          .verdict b { font-size: 10px; letter-spacing: .08em; }

          .stButton button[kind="primary"] { background: #087443; border: none; }
          .stButton button { border-radius: 10px; font-weight: 600; }
          .stTextInput input { border-radius: 10px; }
        </style>
        """
    )


def page_header(title: str, subtitle: str = "") -> None:
    st.html(
        f'<div class="tl-h1">{html.escape(title)}</div>'
        + (f'<div class="tl-sub">{html.escape(subtitle)}</div>' if subtitle else "")
    )


def banner(text_html: str) -> None:
    st.html(f'<div class="tl-banner">{text_html}</div>')


def metric_row(items: list[tuple[str, str, str]]) -> None:
    """items = [(label, value, small)]"""
    cols = st.columns(len(items))
    for col, (label, value, small) in zip(cols, items):
        with col:
            st.html(
                f'<div class="tl-card tl-metric"><label>{html.escape(label)}</label>'
                f'<strong>{value}{f"<small> {html.escape(small)}</small>" if small else ""}</strong></div>'
            )


def format_time(seconds: float | int) -> str:
    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def twitch_ts(url: str, seconds: int) -> str:
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{url}?t={h}h{m}m{s}s"


def scrubber(duration_s: int, moments: list[dict], dead_spots: list[dict],
             url: str = "") -> None:
    """The color-coded VOD bar: gray track, red dead-air spans, colored
    moment markers. Every marker deep-links to the Twitch timestamp."""
    duration_s = max(1, int(duration_s or 1))
    parts = []
    for d in dead_spots or []:
        left = (d["start"] / duration_s) * 100
        width = max(0.4, ((d["end"] - d["start"]) / duration_s) * 100)
        parts.append(
            f'<span class="scrub-seg" title="dead air {format_time(d["start"])}–{format_time(d["end"])}"'
            f' style="left:{left:.2f}%;width:{width:.2f}%;background:{DEAD}"></span>'
        )
    for m in moments or []:
        kind = str(m.get("kind") or "action")
        color = KIND_COLORS.get(kind, "#3B82F6")
        left = (m["start"] / duration_s) * 100
        width = max(0.6, ((m.get("end", m["start"] + 30) - m["start"]) / duration_s) * 100)
        tip = f'{kind} · {format_time(m["start"])} · score {m.get("score", 0):.0f}'
        marker = (f'<span class="scrub-marker" title="{html.escape(tip)}"'
                  f' style="left:{left:.2f}%;width:{width:.2f}%;background:{color}"></span>')
        if url:
            marker = f'<a href="{twitch_ts(url, int(m["start"]))}" target="_blank">{marker}</a>'
        parts.append(marker)

    legend = "".join(
        f'<span><i style="background:{color}"></i>{kind}</span>'
        for kind, color in KIND_COLORS.items()
    ) + f'<span><i style="background:{DEAD}"></i>dead air</span>'

    st.html(
        f"""
        <div class="scrub-wrap"><div class="scrub-track">{''.join(parts)}</div></div>
        <div class="scrub-times"><span>0:00</span><span>{format_time(duration_s // 2)}</span>
        <span>{format_time(duration_s)}</span></div>
        <div class="scrub-legend">{legend}</div>
        """
    )
