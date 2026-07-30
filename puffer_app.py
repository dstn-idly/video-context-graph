"""Puffer AI — viral-moment intelligence for full-length video.

Run with:
    streamlit run puffer_app.py

The interface uses real Neo4j data when the backend is available and falls back
to representative demo data so the product can still be presented standalone.
"""

from __future__ import annotations

import base64
import html
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

from vcg import config, graph, pipeline, twitch  # noqa: E402
from vcg.agent import build_agent  # noqa: E402


st.set_page_config(
    page_title="Puffer AI · Find the Moment",
    page_icon="🐡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

HERO_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "puffer-ocean-hero-v1.jpg"
HERO_IMAGE_URI = (
    "data:image/jpeg;base64,"
    + base64.b64encode(HERO_IMAGE_PATH.read_bytes()).decode("ascii")
)
LOGO_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "puffer-mark.svg"
LOGO_IMAGE_URI = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(LOGO_IMAGE_PATH.read_bytes()).decode("ascii")
)


DEMO_DATA = {
    "stats": {"videos": 1, "scenes": 312, "entities": 84, "topics": 36, "viral_moments": 18},
    "videos": [
        {
            "title": "Kai Cenat — Full Twitch VOD",
            "id": "demo-kai-full-vod",
            "scenes": 312,
            "duration": "3:28:00",
            "source_url": "https://www.twitch.tv/videos/2370838441",
        },
    ],
    "entities": [
        {"name": "Kai Cenat", "count": 118, "type": "person"},
        {"name": "Streamer University", "count": 96, "type": "topic"},
        {"name": "Awards", "count": 54, "type": "topic"},
        {"name": "Surprise", "count": 43, "type": "emotion"},
        {"name": "Community", "count": 39, "type": "topic"},
        {"name": "Celebration", "count": 34, "type": "emotion"},
        {"name": "Reaction", "count": 28, "type": "moment"},
        {"name": "Callback", "count": 17, "type": "context"},
    ],
    "scenes": [
        {
            "title": "The room erupts after an unexpected award",
            "start": 4837,
            "description": "A self-contained surprise reaction with a clean setup and explosive payoff.",
            "score": 96,
            "emotion": "SURPRISE",
        },
        {
            "title": "Kai turns a running joke into a callback",
            "start": 7264,
            "description": "The context graph connects the punchline to an earlier Streamer University moment.",
            "score": 92,
            "emotion": "HUMOR",
        },
        {
            "title": "A heartfelt speech changes the room",
            "start": 11692,
            "description": "Strong emotional contrast, recognizable faces, and a highly quotable closing line.",
            "score": 88,
            "emotion": "WHOLESOME",
        },
        {
            "title": "The celebration breaks into chaos",
            "start": 13930,
            "description": "Fast escalation and visual novelty make this ideal for a short vertical cut.",
            "score": 85,
            "emotion": "EXCITEMENT",
        },
    ],
}


def format_time(seconds: float | int) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# Marker colors for the activity scrubber, tuned for the dark cinematic theme.
KIND_COLORS = {
    "funny": "#ffd166",
    "hype": "#b7ff5c",
    "awkward": "#ff8a5c",
    "tense": "#c08cff",
    "action": "#5cc8ff",
    "visual": "#5cffcd",
}
DEAD_COLOR = "#ff4d5e"


def safe_number(value, spec: str = "{:.0f}", fallback: str = "—") -> str:
    """Format a graph metric that may legitimately be NULL."""
    if value is None:
        return fallback
    try:
        return spec.format(float(value))
    except (TypeError, ValueError):
        return fallback


def condense(text, limit: int = 180) -> str:
    """Collapse model or chat text to one tidy line (still needs escaping)."""
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(collapsed) > limit:
        return collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def vod_id_from(value) -> str:
    """Numeric Twitch VOD id out of a URL, a 'twitch:<id>' key, or a bare id.

    An all-zero id is treated as absent — placeholder rows in the graph carry
    .../videos/0 and must not turn into a selectable stream.
    """
    text = str(value or "").strip()
    match = re.search(r"twitch\.tv/videos/(\d+)", text) or re.fullmatch(
        r"(?:twitch:)?v?(\d+)", text
    )
    if not match:
        return ""
    vid = match.group(1)
    return vid if vid.strip("0") else ""


def safe_http_url(url) -> str:
    """Only ever emit http(s) hrefs — a graph row is not a trusted source."""
    text = str(url or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def twitch_timestamp_url(url: str, seconds) -> str:
    """Deep link into a VOD at an absolute offset: ...?t=1h02m03s."""
    base = safe_http_url(url).split("?", 1)[0]
    if not base:
        return ""
    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{base}?t={hours}h{minutes}m{secs}s"


def notice(message: str, *, tone: str = "ok") -> None:
    """Status line in the existing .source-status language — never a traceback."""
    if not message:
        return
    style = ""
    if tone == "error":
        style = (
            ' style="border-left-color:#ff4d5e;color:#ffc7cc;'
            'background:rgba(255,77,94,.06)"'
        )
    st.html(f'<div class="source-status"{style}>{html.escape(str(message))}</div>')


def demo_answer(prompt: str, profile: str = "") -> str:
    """Interactive fallback that is clearly labeled as representative demo data."""
    lowered = prompt.lower()
    candidates = DEMO_DATA["scenes"]
    emotion_matches = [
        scene for scene in candidates
        if str(scene.get("emotion", "")).lower() in lowered
    ]
    ranked = (emotion_matches or candidates)[:3]
    lines = ["Demo analysis — strongest full-VOD clip opportunities:"]
    for scene in ranked:
        lines.append(
            f"{scene['score']}/100 at {format_time(scene['start'])} · "
            f"{scene['title']} — {scene['description']}"
        )
    lines.append(
        "Live mode replaces these representative candidates with observations "
        "and connected full-stream context from the selected VOD."
    )
    if any(
        phrase in lowered
        for phrase in (
            "personality",
            "my format",
            "for me",
            "go viral",
            "teach me",
            "recreate",
            "viral mechanic",
        )
    ):
        lesson = creator_playbook(profile)
        lines.extend(
            [
                "",
                f"Your translation: {lesson['delivery'].title()}.",
                lesson["lesson"],
                f'Opening hook: “{lesson["opening"]}”',
            ]
        )
    return "\n\n".join(lines)


def creator_playbook(profile: str) -> dict[str, str]:
    """Translate proven viral mechanics into the creator's natural delivery."""
    lowered = profile.lower()
    if any(word in lowered for word in ("calm", "educational", "analytical")):
        delivery = "calm authority with a sharp visual reveal"
        opening = "I tested whether AI can find the moment everyone else misses."
    else:
        delivery = "high-energy build-in-public tension with an honest payoff"
        opening = "I gave this AI 45 minutes to find a viral moment in a 3-hour stream."

    return {
        "delivery": delivery,
        "pattern": (
            "Instantly legible stakes → recognizable personality → escalating reaction "
            "→ a payoff that works without the full stream → a reason to share."
        ),
        "lesson": (
            "Your edge is not impersonating Kai. It is using your natural urgency, blunt "
            "decision-making, and builder energy as the entertainment. Show the timer, "
            "the failure, and the moment the product finally works."
        ),
        "opening": opening,
        "script": (
            "0–2s · Open with the challenge and a visible countdown.\n"
            "2–8s · Show the full VOD and the impossible amount of footage.\n"
            "8–17s · Let Puffer surface three competing moments; react honestly.\n"
            "17–25s · Reveal the winning moment and the graph path explaining why.\n"
            "25–30s · Offer the moment as a bounty: “Clip it. If it hits, we both win.”"
        ),
        "title": "I Gave AI 45 Minutes to Find a Viral Moment",
    }


@st.cache_data(ttl=20, show_spinner=False)
def load_context_data() -> tuple[dict, bool, str]:
    """Read the dashboard projection from Neo4j, with a demo-safe fallback."""
    try:
        if (
            not config.NEO4J_PASSWORD
            or "xxxx" in config.NEO4J_URI
            or config.NEO4J_PASSWORD.startswith("<")
        ):
            return DEMO_DATA, False, "Demo signal · backend ready to connect"
        stats = graph.stats()
        moment_rows = graph.run_cypher(
            "MATCH (m:ViralMoment) WHERE m.score >= 60 RETURN count(m) AS count"
        )
        stats["viral_moments"] = moment_rows[0]["count"] if moment_rows else 0
        if not stats.get("viral_moments"):
            # No Pegasus scene pass on this graph yet — the chat detector's own
            # high scorers are the honest stand-in for "candidates worth cutting".
            chat_rows = graph.run_cypher(
                "MATCH (m:Moment) WHERE m.score >= 60 RETURN count(m) AS count"
            )
            stats["viral_moments"] = chat_rows[0]["count"] if chat_rows else 0
        videos = graph.run_cypher(
            """
            MATCH (v:Video)
            OPTIONAL MATCH (v)-[:HAS_SCENE]->(s:Scene)
            OPTIONAL MATCH (v)-[:HAS_MOMENT]->(m:Moment)
            RETURN v.video_id AS id, v.title AS title, v.source_url AS source_url,
                   v.duration_s AS duration_s, v.msgs_per_min AS msgs_per_min,
                   v.dead_pct AS dead_pct, count(DISTINCT s) AS scenes,
                   count(DISTINCT m) AS moments, max(s.end) AS scene_end
            ORDER BY moments DESC, scenes DESC
            LIMIT 8
            """
        )
        entities = graph.run_cypher(
            """
            MATCH (e:Entity)<-[:MENTIONS]-(s:Scene)
            RETURN e.name AS name, coalesce(e.type, 'entity') AS type,
                   count(DISTINCT s) AS count
            ORDER BY count DESC
            LIMIT 12
            """
        )
        scenes = graph.run_cypher(
            """
            MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:HAS_VIRAL_MOMENT]->(m:ViralMoment)
            RETURN m.clip_title AS title, m.start AS start, m.score AS score,
                   m.emotion AS emotion, m.why_viral AS description
            ORDER BY m.score DESC
            LIMIT 8
            """
        )
        # Real per-VOD performance rows and the real cross-stream leaderboard,
        # TwelveLabs verdict and detector included.
        performance = graph.performance_overview()
        best_moments = graph.top_moments(12)

        if not videos:
            return DEMO_DATA, False, "Graph connected · waiting for first ingest"

        for video in videos:
            video["duration"] = format_time(
                video.get("duration_s") or video.get("scene_end") or 0
            )
            video["source_url"] = str(video.get("source_url") or "")
            video["count_label"] = (
                f"{int(video.get('scenes') or 0)} SCENES"
                if video.get("scenes")
                else f"{int(video.get('moments') or 0)} MOMENTS"
            )

        if not scenes:
            # Nothing has been through the Pegasus scene pass, so rank the real
            # chat/visual moments instead of falling back to canned copy.
            scenes = [
                {
                    "title": condense(
                        row.get("ai_verdict") or row.get("reason") or "Chat spiked here",
                        90,
                    ),
                    "start": row.get("start") or 0,
                    "score": row.get("score") or 0,
                    "emotion": str(row.get("kind") or "moment").upper(),
                    "url": str(row.get("url") or ""),
                    "description": condense(
                        " · ".join(
                            part
                            for part in [
                                str(row.get("title") or "Untitled stream"),
                                "seen by "
                                + str(row.get("detector") or "chat").replace(
                                    "twelvelabs", "TwelveLabs"
                                ),
                                " ".join(row.get("sample") or [])[:160],
                            ]
                            if part
                        ),
                        170,
                    ),
                }
                for row in best_moments[:8]
            ]

        return {
            "stats": stats,
            "videos": videos,
            "entities": entities,
            "scenes": scenes,
            "performance": performance,
            "moments": best_moments,
        }, True, "Context engine online"
    except Exception as exc:
        return DEMO_DATA, False, f"Demo signal · {type(exc).__name__}"


@st.cache_data(ttl=20, show_spinner=False)
def load_vod_timeline(video_id: str) -> tuple[list, list]:
    """Moments + dead spots for one video. Empty rather than raising."""
    if not video_id:
        return [], []
    try:
        return graph.video_moments(video_id), graph.video_dead_spots(video_id)
    except Exception:
        return [], []


def render_graph(entities: list[dict]) -> None:
    """Render a dependency-free, responsive context-graph preview."""
    nodes = entities[:8] or DEMO_DATA["entities"][:8]
    width, height = 760, 430
    center_x, center_y = width / 2, height / 2
    radii = [165, 155, 170, 150, 168, 158, 172, 152]
    positions: list[tuple[float, float]] = []

    for index, _ in enumerate(nodes):
        angle = (-math.pi / 2) + (2 * math.pi * index / len(nodes))
        radius = radii[index % len(radii)]
        positions.append(
            (center_x + math.cos(angle) * radius, center_y + math.sin(angle) * radius)
        )

    lines = []
    labels = []
    max_count = max((int(node.get("count", 1)) for node in nodes), default=1)
    for index, (node, (x, y)) in enumerate(zip(nodes, positions)):
        count = int(node.get("count", 1))
        size = 7 + (count / max_count) * 8
        color = "#b7ff5c" if index < 3 else "#7b8798"
        angle = math.degrees(math.atan2(y - center_y, x - center_x))
        distance_pct = math.hypot(x - center_x, y - center_y) / width * 100
        lines.append(
            f'<i class="edge" style="width:{distance_pct:.2f}%;transform:rotate({angle:.2f}deg)"></i>'
        )
        safe_name = html.escape(str(node.get("name", "Unknown")))
        labels.append(
            f"""
            <div class="node" style="left:{x / width * 100:.2f}%;top:{y / height * 100:.2f}%">
              <i style="width:{size * 2:.1f}px;height:{size * 2:.1f}px;background:{color}"></i>
              <b>{safe_name}</b>
              <small>{count} scenes</small>
            </div>
            """
        )

    graph_markup = f"""
    <div class="graph-shell">
      <div class="scanline"></div>
      <div class="edges">{''.join(lines)}</div>
      <div class="graph-core"><strong>PUFFER</strong><small>VIRAL GRAPH</small></div>
      {''.join(labels)}
      <div class="graph-key"><i></i> WHY THIS MOMENT WILL TRAVEL</div>
    </div>
    <style>
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; background: transparent; overflow: hidden; }}
      .graph-shell {{
        position: relative; height: 430px; overflow: hidden;
        border: 1px solid rgba(255,255,255,.08); border-radius: 18px;
        background:
          radial-gradient(circle at 50% 48%, rgba(151,235,62,.09), transparent 29%),
          linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px),
          #0c1015;
        background-size: auto, 32px 32px, 32px 32px, auto;
      }}
      .edge {{
        position: absolute; z-index: 0; left: 50%; top: 50%; height: 1px;
        transform-origin: left center; opacity: .65;
        background: repeating-linear-gradient(90deg, rgba(183,255,92,.38) 0 4px, transparent 4px 10px);
      }}
      .graph-core {{
        position: absolute; z-index: 1; left: 50%; top: 50%;
        display: grid; place-content: center; width: 68px; height: 68px;
        transform: translate(-50%,-50%); border-radius: 99px;
        color: #11170b; text-align: center;
        background: radial-gradient(circle at 40% 35%, #dcffa7, #8fe229);
        box-shadow: 0 0 0 18px rgba(183,255,92,.04), 0 0 32px rgba(183,255,92,.25);
      }}
      .graph-core strong {{ font: 800 11px Inter,sans-serif; letter-spacing: .14em; }}
      .graph-core small {{ margin-top: 2px; font: 700 7px Inter,sans-serif; letter-spacing: .08em; }}
      .node {{
        position: absolute; z-index: 1; display: flex; flex-direction: column; align-items: center;
        width: 120px; transform: translate(-50%,-50%); color: #dce3eb;
        font-family: Inter,ui-sans-serif,system-ui; text-align: center;
      }}
      .node i {{ display:block; border-radius:99px; box-shadow:0 0 0 8px rgba(183,255,92,.06); }}
      .node b {{ margin-top: 10px; font-size: 14px; font-weight: 650; }}
      .node small {{ margin-top: 2px; color:#798697; font-size:10px; letter-spacing:.08em; text-transform:uppercase; }}
      .graph-key {{
        position: absolute; left: 18px; bottom: 14px; color: #66717f;
        font: 600 11px Inter, sans-serif; letter-spacing: .12em;
      }}
      .graph-key i {{
        display: inline-block; width: 6px; height: 6px; margin-right: 7px;
        border-radius: 99px; background: #b7ff5c; box-shadow: 0 0 10px #b7ff5c;
      }}
      .scanline {{
        position: absolute; z-index: 2; inset: -40% 0 auto; height: 40%;
        background: linear-gradient(transparent, rgba(183,255,92,.025), transparent);
        animation: scan 7s linear infinite; pointer-events: none;
      }}
      @keyframes scan {{ to {{ transform: translateY(350%); }} }}
    </style>
    """
    st.html(graph_markup)


def render_scrubber(duration_s, moments: list[dict], dead_spots: list[dict],
                    source_url: str = "") -> None:
    """Color-coded activity bar for one VOD.

    Red spans are dead air, colored markers are detected moments, and every
    marker is a deep link into that exact second of the Twitch VOD. Every
    interpolated value is escaped — titles, chat samples and TwelveLabs
    verdicts are all third-party text.
    """
    duration = max(1, int(duration_s or 0))
    pieces: list[str] = []

    for spot in dead_spots or []:
        start = max(0, int(spot.get("start") or 0))
        end = max(start, int(spot.get("end") or start))
        left = max(0.0, min(99.6, start / duration * 100))
        width = max(0.35, min(100.0 - left, (end - start) / duration * 100))
        tip = (
            f"Dead air {format_time(start)}–{format_time(end)} · "
            f"{safe_number(spot.get('severity'), '{:.0f}')}% below this stream's baseline"
        )
        pieces.append(
            f'<span class="puffer-dead" title="{html.escape(tip)}"'
            f' style="left:{left:.3f}%;width:{width:.3f}%"></span>'
        )

    counts: dict[str, int] = {}
    for moment in moments or []:
        kind = str(moment.get("kind") or "action").lower()
        counts[kind] = counts.get(kind, 0) + 1
        color = KIND_COLORS.get(kind, KIND_COLORS["action"])
        start = max(0, int(moment.get("start") or 0))
        end = max(start, int(moment.get("end") or (start + 30)))
        left = max(0.0, min(99.4, start / duration * 100))
        width = max(0.6, min(100.0 - left, (end - start) / duration * 100))
        detector = str(moment.get("detector") or "chat")
        detail = condense(moment.get("ai_verdict") or moment.get("reason"), 150)
        tip = " · ".join(
            part
            for part in [
                kind.upper(),
                format_time(start),
                f"score {safe_number(moment.get('score'), '{:.0f}')}",
                "TwelveLabs watched it" if detector == "twelvelabs" else "chat detected",
                detail,
            ]
            if part
        )
        style = f"left:{left:.3f}%;width:{width:.3f}%;background:{color};color:{color}"
        target = twitch_timestamp_url(source_url, start)
        if target:
            href = html.escape(target, quote=True)
            pieces.append(
                f'<a class="puffer-mark" href="{href}" target="_blank" rel="noopener"'
                f' title="{html.escape(tip)}" style="{style}"></a>'
            )
        else:
            pieces.append(
                f'<i class="puffer-mark" title="{html.escape(tip)}" style="{style}"></i>'
            )

    legend = "".join(
        f'<span><i style="background:{color}"></i>{html.escape(kind.upper())}'
        f'{f" · {counts[kind]}" if counts.get(kind) else ""}</span>'
        for kind, color in KIND_COLORS.items()
    )
    legend += (
        f'<span><i style="background:{DEAD_COLOR}"></i>DEAD AIR'
        f'{f" · {len(dead_spots)}" if dead_spots else ""}</span>'
    )

    st.html(
        f"""
        <div class="puffer-scrub">{''.join(pieces)}</div>
        <div class="puffer-scrub-times">
          <span>0:00</span><span>{html.escape(format_time(duration // 2))}</span>
          <span>{html.escape(format_time(duration))}</span>
        </div>
        <div class="puffer-legend">{legend}</div>
        <style>
          .puffer-scrub {{
            position: relative; height: 38px; margin: 2px 0 7px; overflow: hidden;
            border: 1px solid rgba(255,255,255,.08); border-radius: 13px;
            background:
              linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px),
              linear-gradient(145deg, rgba(17,22,29,.94), rgba(10,14,19,.96));
            background-size: 6.25% 100%, auto;
          }}
          .puffer-dead {{
            position: absolute; top: 0; bottom: 0;
            background: rgba(255,77,94,.28); border-left: 1px solid rgba(255,77,94,.5);
          }}
          .puffer-mark {{
            position: absolute; top: 7px; bottom: 7px; display: block; min-width: 4px;
            border-radius: 3px; text-decoration: none; opacity: .9;
            box-shadow: 0 0 12px rgba(0,0,0,.55);
          }}
          .puffer-mark:hover {{ top: 3px; bottom: 3px; opacity: 1; box-shadow: 0 0 16px currentColor; }}
          .puffer-scrub-times {{
            display: flex; justify-content: space-between;
            color: #5f6b79; font: 500 12px "DM Mono", monospace; letter-spacing: .08em;
          }}
          .puffer-legend {{
            display: flex; flex-wrap: wrap; gap: 15px; margin: 11px 0 2px;
            color: #7b8796; font: 600 11px "DM Mono", monospace; letter-spacing: .1em;
          }}
          .puffer-legend span {{ display: inline-flex; align-items: center; gap: 7px; }}
          .puffer-legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; }}
        </style>
        """
    )


st.html(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap');

      :root {
        --ink: #eef2f6;
        --muted: #8195a8;
        --line: rgba(146, 214, 255, .11);
        --panel: rgba(7, 19, 31, .9);
        --acid: #b7ff5c;
        --acid-soft: rgba(183, 255, 92, .12);
        --ocean: #061525;
        --deep-ocean: #030914;
      }

      html, body, [class*="css"] {
        font-family: "Manrope", sans-serif;
        color-scheme: dark !important;
        background: var(--deep-ocean) !important;
      }
      .stApp {
        color: var(--ink);
        background:
          radial-gradient(circle at 82% 0%, rgba(34,157,213,.12), transparent 27%),
          radial-gradient(circle at 0% 55%, rgba(22,96,139,.13), transparent 31%),
          linear-gradient(180deg, #06101d, var(--deep-ocean) 58%);
      }
      [data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent; }
      #MainMenu, [data-testid="stDeployButton"],
      [data-testid="stAppDeployButton"] { display: none !important; }
      [data-testid="stSidebar"] { display: none; }
      [data-testid="stBottom"], [data-testid="stBottomBlockContainer"],
      [data-testid="stChatInputContainer"] {
        background: var(--deep-ocean) !important;
      }
      .block-container { position: relative; max-width: 1480px; padding: 1.6rem 2.2rem 3rem; }

      .spire-nav {
        display: flex; align-items: center; justify-content: space-between;
        position: relative; z-index: 80; min-height: 64px; padding: 4px 0;
      }
      .nav-divider { height: 1px; margin: 9px 0 0; background: var(--line); }
      .brand { display: flex; align-items: center; gap: 12px; }
      .brand-mark {
        display: grid; place-items: center; width: 40px; height: 40px;
        position: relative; border-radius: 12px;
        background: linear-gradient(145deg, #dcff9e 0%, #aef04d 48%, #75b824 100%);
        transform: perspective(180px) rotateX(7deg) rotateY(-10deg);
        box-shadow:
          0 6px 0 #537f1d,
          0 10px 18px rgba(0,0,0,.45),
          inset 2px 2px 4px rgba(255,255,255,.55),
          inset -3px -3px 6px rgba(54,93,12,.35),
          0 0 28px rgba(183,255,92,.22);
      }
      .brand-mark svg {
        width: 29px; height: 29px; color: #071009;
        filter: drop-shadow(1px 2px 1px rgba(255,255,255,.18));
      }
      .brand-mark:after {
        content: ""; position: absolute; inset: 3px 4px auto;
        height: 1px; border-radius: 99px; background: rgba(255,255,255,.65);
      }
      .brand-name { font-size: 15px; font-weight: 800; letter-spacing: .17em; }
      .brand-name small {
        display: block; color: #566171; font: 500 8px "DM Mono", monospace;
        letter-spacing: .18em; margin-top: 2px;
      }
      .system-state {
        display: flex; align-items: center; gap: 8px; color: #7d8896;
        font: 500 9px "DM Mono", monospace; letter-spacing: .1em;
      }
      .system-state .pulse {
        width: 7px; height: 7px; border-radius: 99px; background: var(--acid);
        box-shadow: 0 0 12px var(--acid); animation: pulse 2s ease-in-out infinite;
      }
      .nav-actions { display: flex; align-items: center; gap: 18px; }
      .demo-access-button {
        display: grid; place-items: center; min-width: 188px; min-height: 54px;
        padding: 0 18px; border: 1px solid var(--acid); border-radius: 11px;
        color: #0b1007; background: var(--acid); font-size: 14px; font-weight: 800;
        letter-spacing: .03em; text-decoration: none; box-shadow: 0 10px 28px rgba(183,255,92,.14);
      }
      .demo-access-button:hover { border-color: #d4ff9d; color: #0b1007; background: #c7ff80; }
      .demo-login-band {
        display: grid; grid-template-columns: 1fr 1.2fr; align-items: center; gap: 30px;
        margin: 24px 0 30px; padding: 28px; border: 1px solid rgba(183,255,92,.22);
        border-radius: 18px; background:
          linear-gradient(105deg, rgba(183,255,92,.1), rgba(13,18,24,.96) 48%);
        scroll-margin-top: 20px;
      }
      .demo-login-copy small { color: var(--acid); font: 700 12px "DM Mono",monospace; letter-spacing: .12em; }
      .demo-login-copy h2 { margin: 9px 0 6px; color: #eef2f6; font-size: 30px; }
      .demo-login-copy p { margin: 0; color: #8f9ba9; font-size: 16px; line-height: 1.55; }
      .demo-credentials { display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; align-items: stretch; }
      .credential {
        display: flex; flex-direction: column; justify-content: center; min-height: 66px;
        padding: 10px 13px; border: 1px solid rgba(255,255,255,.1); border-radius: 10px;
        color: #e9edf1; background: #090d12;
      }
      .credential label { color: #687585; font: 700 10px "DM Mono",monospace; letter-spacing: .1em; }
      .credential b { margin-top: 5px; font-size: 15px; }
      .demo-enter {
        display: grid; place-items: center; min-height: 66px; padding: 0 17px;
        border-radius: 10px; color: #0b1007; background: var(--acid);
        font-size: 14px; font-weight: 800; text-decoration: none;
      }
      @keyframes pulse { 50% { opacity: .35; } }

      .hero { padding: 48px 0 30px; }
      .eyebrow {
        color: var(--acid); font: 500 10px "DM Mono", monospace;
        letter-spacing: .16em; text-transform: uppercase;
      }
      .hero h1 {
        max-width: 770px; margin: 13px 0 14px; color: #f5f7f9;
        font-size: clamp(42px, 5.3vw, 76px); line-height: .98; letter-spacing: -.055em;
      }
      .hero h1 span { color: #596473; }
      .hero p { max-width: 650px; color: #8793a2; font-size: 14px; line-height: 1.7; }

      .metric-row {
        display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
        margin: 4px 0 26px; border: 1px solid var(--line);
        border-radius: 15px; overflow: hidden; background: var(--line);
      }
      .metric { padding: 17px 20px; background: #0d1117; }
      .metric label {
        color: #5d6877; font: 500 8px "DM Mono", monospace; letter-spacing: .15em;
      }
      .metric strong {
        display: block; margin-top: 6px; color: #e9edf1;
        font: 700 22px "DM Mono", monospace;
      }

      .section-kicker {
        color: #717d8c; font: 500 9px "DM Mono", monospace;
        letter-spacing: .16em; text-transform: uppercase; margin: 3px 0 12px;
      }
      .panel {
        min-height: 430px; padding: 18px; border: 1px solid var(--line);
        border-radius: 18px; background: linear-gradient(145deg, rgba(17,22,29,.94), rgba(10,14,19,.96));
      }
      .panel-head {
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 18px;
      }
      .panel-title { font-size: 13px; font-weight: 700; }
      .panel-meta {
        color: #596575; font: 500 8px "DM Mono", monospace; letter-spacing: .08em;
      }
      .video-card {
        margin-bottom: 10px; padding: 13px; border: 1px solid rgba(255,255,255,.06);
        border-radius: 12px; background: rgba(255,255,255,.018);
      }
      .video-card:hover { border-color: rgba(183,255,92,.25); background: rgba(183,255,92,.025); }
      .video-top { display: flex; gap: 11px; align-items: flex-start; }
      .video-icon {
        display: grid; flex: 0 0 31px; place-items: center; width: 31px; height: 31px;
        border-radius: 8px; color: var(--acid); background: var(--acid-soft);
        font-size: 10px;
      }
      .video-title { color: #dce2e8; font-size: 11px; font-weight: 650; line-height: 1.35; }
      .video-meta { margin-top: 5px; color: #5f6b79; font: 400 8px "DM Mono", monospace; }
      .bar { height: 2px; margin-top: 12px; overflow: hidden; border-radius: 2px; background: #202731; }
      .bar i { display: block; height: 100%; background: linear-gradient(90deg, #769f43, var(--acid)); }
      .source-link {
        display: inline-block; margin-top: 14px; color: var(--acid);
        font: 500 8px "DM Mono", monospace; letter-spacing: .08em; text-decoration: none;
      }

      .scene {
        position: relative; padding: 0 0 21px 22px; border-left: 1px solid #252d37;
      }
      .scene:last-child { border-left-color: transparent; }
      .scene:before {
        content: ""; position: absolute; left: -4px; top: 2px; width: 7px; height: 7px;
        border-radius: 99px; background: #3e4856; border: 2px solid #11161c;
      }
      .scene:first-of-type:before { background: var(--acid); box-shadow: 0 0 10px rgba(183,255,92,.5); }
      .scene-time { color: var(--acid); font: 500 8px "DM Mono", monospace; }
      .scene-title { margin: 5px 0; color: #cbd2da; font-size: 10px; font-weight: 650; }
      .scene-copy { color: #687585; font-size: 9px; line-height: 1.5; }

      .ask-panel {
        margin-top: 26px; padding: 24px 26px 15px; border: 1px solid var(--line);
        border-radius: 18px;
        background: linear-gradient(120deg, rgba(183,255,92,.055), rgba(13,17,23,.92) 34%);
      }
      .ask-label { color: var(--acid); font: 500 9px "DM Mono", monospace; letter-spacing: .16em; }
      .ask-title { margin: 8px 0 0; font-size: 23px; font-weight: 750; letter-spacing: -.025em; }
      .bounty-panel {
        display: flex; align-items: center; justify-content: space-between; gap: 24px;
        margin-top: 26px; padding: 18px 24px; border: 1px solid rgba(183,255,92,.18);
        border-radius: 16px; background: linear-gradient(100deg, rgba(183,255,92,.09), rgba(13,17,23,.94) 42%);
      }
      .bounty-copy small { color: var(--acid); font: 500 8px "DM Mono", monospace; letter-spacing: .15em; }
      .bounty-copy strong { display: block; margin: 5px 0; color: #edf2f6; font-size: 16px; }
      .bounty-copy span { color: #768292; font-size: 10px; }
      .bounty-stats { display: flex; gap: 28px; text-align: right; }
      .bounty-stats b { display: block; color: var(--acid); font: 700 18px "DM Mono", monospace; }
      .bounty-stats label { color: #66717f; font: 500 7px "DM Mono", monospace; letter-spacing: .1em; }
      .player-copy {
        min-height: 520px; padding: 30px; border: 1px solid var(--line);
        border-radius: 18px; background: linear-gradient(145deg, rgba(18,24,31,.98), rgba(11,15,20,.98));
      }
      .player-copy small { color: var(--acid); font: 500 8px "DM Mono", monospace; letter-spacing: .15em; }
      .player-copy h3 { margin: 14px 0 10px; color: #edf2f6; font-size: 24px; line-height: 1.15; }
      .player-copy p { color: #7c8896; font-size: 11px; line-height: 1.65; }
      .pipeline-step { margin-top: 16px; padding: 11px 12px; border-left: 2px solid #384451; color: #8d99a8; font-size: 10px; }
      .pipeline-step.live { border-color: var(--acid); color: #d8e0e7; background: rgba(183,255,92,.04); }
      .landing-hero {
        position: relative; display: grid; grid-template-columns: 1fr;
        align-items: center; min-height: 720px; margin-top: 26px; padding: 76px 54px;
        overflow: hidden; border: 1px solid rgba(146,214,255,.18); border-radius: 28px;
        isolation: isolate; box-shadow: 0 38px 90px rgba(0,0,0,.42);
      }
      .landing-hero:after {
        content: ""; position: absolute; z-index: -1; inset: 0;
        background: linear-gradient(180deg, rgba(125,216,255,.08), transparent 22%, transparent 72%, rgba(1,8,17,.34));
        pointer-events: none;
      }
      .landing-copy { position: relative; z-index: 3; width: min(57%, 760px); }
      .landing-copy .eyebrow { margin-bottom: 20px; }
      .landing-copy h1 {
        max-width: 720px; margin: 0; color: #f7fbff;
        font-size: clamp(62px, 7vw, 106px); line-height: .87; letter-spacing: -.07em;
        text-shadow: 0 10px 42px rgba(0,0,0,.52);
      }
      .landing-copy h1 span {
        color: #b7ff5c;
        text-shadow: 0 0 40px rgba(183,255,92,.22);
      }
      .landing-copy p {
        max-width: 680px; margin: 30px 0 0; color: #c2d2df;
        font-size: 21px; line-height: 1.65; text-shadow: 0 3px 18px rgba(0,0,0,.8);
      }
      .landing-cta-row { display: flex; align-items: center; gap: 14px; margin-top: 34px; }
      .landing-cta {
        display: inline-flex; align-items: center; min-height: 54px; padding: 0 22px;
        border-radius: 12px; color: #11170b; background: var(--acid);
        font-size: 14px; font-weight: 800; letter-spacing: .04em; text-decoration: none;
        box-shadow: 0 10px 28px rgba(183,255,92,.15), inset 0 1px rgba(255,255,255,.45);
      }
      .landing-note { color: #677382; font: 600 11px "DM Mono", monospace; letter-spacing: .08em; }
      .signal-stage {
        position: absolute; z-index: 2; inset: 0; pointer-events: none;
      }
      .float-card {
        position: absolute; z-index: 2; min-width: 172px; padding: 13px 16px;
        border: 1px solid rgba(185,231,255,.25); border-radius: 13px;
        color: #eef8ff; background: rgba(4,19,33,.68);
        box-shadow: 0 18px 40px rgba(0,0,0,.32); backdrop-filter: blur(16px);
      }
      .float-card small { color: #7c8997; font: 600 10px "DM Mono", monospace; letter-spacing: .1em; }
      .float-card strong { display:block; margin-top:6px; font-size:16px; }
      .float-card em { color:var(--acid); font-style:normal; }
      .float-card.one { top:8%; right:4%; }
      .float-card.two { right:22%; bottom:7%; }
      .float-card.three { right:3%; bottom:16%; }
      .landing-proof {
        display:flex; gap:26px; padding:22px 0 34px; color:#717d8b;
        font:600 12px "DM Mono",monospace; letter-spacing:.06em;
      }
      .landing-proof b { color:#dce3e9; font-size:15px; }
      .public-feature-grid {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px;
        margin: 34px 0 110px;
      }
      .public-feature {
        min-height: 330px; padding: 30px; border: 1px solid var(--line);
        border-radius: 17px;
        background: linear-gradient(145deg, rgba(10,30,46,.94), rgba(4,13,24,.97));
      }
      .public-feature small {
        color: var(--acid); font: 700 12px "DM Mono",monospace; letter-spacing: .13em;
      }
      .public-feature h3 { margin: 38px 0 14px; color: #eef2f5; font-size: 27px; }
      .public-feature p { margin: 0; color: #8995a3; font-size: 16px; line-height: 1.65; }
      .public-feature ul {
        display: grid; gap: 9px; margin: 24px 0 0; padding: 0; list-style: none;
        color: #b3c1cd; font-size: 14px; line-height: 1.45;
      }
      .public-feature li:before { content: "↳"; margin-right: 9px; color: var(--acid); }
      .story-section { margin: 0 0 110px; }
      .story-head {
        display: grid; grid-template-columns: .72fr 1.28fr; gap: 80px;
        align-items: end; margin-bottom: 42px;
      }
      .story-head small, .index-copy > small, .final-cta small {
        color: var(--acid); font: 700 13px "DM Mono", monospace;
        letter-spacing: .15em; text-transform: uppercase;
      }
      .story-head h2, .index-copy h2 {
        margin: 13px 0 0; color: #f2f7fb; font-size: clamp(42px,5.2vw,72px);
        line-height: .97; letter-spacing: -.055em;
      }
      .story-head p {
        max-width: 680px; margin: 0; color: #9eafbd; font-size: 20px; line-height: 1.7;
      }
      .process-grid {
        display: grid; grid-template-columns: repeat(4,1fr); gap: 1px;
        overflow: hidden; border: 1px solid var(--line); border-radius: 20px;
        background: var(--line);
      }
      .process-step {
        position: relative; min-height: 310px; padding: 30px 28px;
        background: linear-gradient(160deg, rgba(10,30,46,.98), rgba(3,12,22,.98));
      }
      .process-step:after {
        content: "→"; position: absolute; z-index: 2; top: 28px; right: -13px;
        display: grid; place-items: center; width: 26px; height: 26px; border-radius: 99px;
        color: #08120b; background: var(--acid); font-weight: 900;
      }
      .process-step:last-child:after { display: none; }
      .process-step b {
        display: block; color: #63829a; font: 700 14px "DM Mono",monospace;
      }
      .process-step h3 { margin: 55px 0 13px; color: #edf5fa; font-size: 25px; line-height: 1.1; }
      .process-step p { margin: 0; color: #8fa2b2; font-size: 15px; line-height: 1.65; }
      .process-step em {
        display: inline-block; margin-top: 22px; padding: 7px 10px;
        border: 1px solid rgba(183,255,92,.18); border-radius: 99px;
        color: var(--acid); background: rgba(183,255,92,.05);
        font: 700 10px "DM Mono",monospace; font-style: normal; letter-spacing: .08em;
      }
      .index-section {
        margin: 0 0 110px; padding: 46px; border: 1px solid rgba(135,210,255,.17);
        border-radius: 26px; background:
          radial-gradient(circle at 82% 30%, rgba(27,124,176,.15), transparent 32%),
          linear-gradient(145deg, rgba(7,27,43,.97), rgba(3,12,22,.99));
        box-shadow: 0 34px 70px rgba(0,0,0,.26);
      }
      .index-layout { display: grid; grid-template-columns: .72fr 1.28fr; gap: 44px; align-items: center; }
      .index-copy p {
        max-width: 570px; margin: 22px 0 0; color: #9aabba; font-size: 18px; line-height: 1.7;
      }
      .character-list {
        display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 28px;
      }
      .characteristic {
        padding: 13px 14px; border: 1px solid rgba(255,255,255,.07);
        border-radius: 11px; color: #c4d2dc; background: rgba(255,255,255,.025);
        font-size: 13px; font-weight: 700;
      }
      .characteristic i {
        display: inline-block; width: 7px; height: 7px; margin-right: 9px;
        border-radius: 99px; background: var(--acid); box-shadow: 0 0 10px rgba(183,255,92,.35);
      }
      .index-graph {
        position: relative; min-height: 590px; overflow: hidden;
        border: 1px solid rgba(135,210,255,.14); border-radius: 20px;
        background:
          linear-gradient(rgba(134,206,246,.045) 1px, transparent 1px),
          linear-gradient(90deg, rgba(134,206,246,.045) 1px, transparent 1px),
          rgba(2,11,20,.72);
        background-size: 32px 32px;
      }
      .index-graph:before {
        content: "LIVE VIDEO CHARACTERISTIC INDEX"; position: absolute; top: 20px; left: 22px;
        color: #59748a; font: 700 11px "DM Mono",monospace; letter-spacing: .13em;
      }
      .index-wire { position: absolute; z-index: 1; background: rgba(113,184,224,.35); }
      .index-wire.h { height: 1px; }
      .index-wire.v { width: 1px; }
      .wire-a { left: 17%; top: 50%; width: 10%; }
      .wire-b { left: 27%; top: 21%; height: 58%; }
      .wire-c { left: 27%; top: 50%; width: 22%; }
      .wire-d { left: 49%; top: 13%; height: 74%; }
      .wire-e { left: 49%; top: 50%; width: 24%; }
      .wire-f { left: 73%; top: 42%; height: 16%; }
      .wire-g { left: 73%; top: 50%; width: 14%; }
      .index-node {
        position: absolute; z-index: 2; min-width: 112px; padding: 12px 13px;
        border: 1px solid rgba(133,203,242,.2); border-radius: 11px;
        color: #deebf3; background: rgba(5,22,36,.94);
        box-shadow: 0 13px 28px rgba(0,0,0,.28); transform: translateY(-50%);
      }
      .index-node b { display: block; font-size: 13px; line-height: 1.15; }
      .index-node small {
        display: block; margin-top: 5px; color: #6e8799;
        font: 700 9px "DM Mono",monospace; letter-spacing: .07em;
      }
      .index-node.source {
        left: 3%; top: 50%; color: #0a140c; border-color: var(--acid);
        background: var(--acid);
      }
      .index-node.source small { color: #426123; }
      .index-node.s1 { left: 20%; top: 21%; }
      .index-node.s2 { left: 20%; top: 50%; }
      .index-node.s3 { left: 20%; top: 79%; }
      .index-node.c1 { left: 42%; top: 13%; }
      .index-node.c2 { left: 42%; top: 31%; }
      .index-node.c3 { left: 42%; top: 50%; }
      .index-node.c4 { left: 42%; top: 69%; }
      .index-node.c5 { left: 42%; top: 87%; }
      .index-node.profile {
        left: 66%; top: 42%; border-color: rgba(183,255,92,.35);
        background: rgba(25,52,39,.96);
      }
      .index-node.output {
        left: 82%; top: 58%; color: #0a140c; border-color: var(--acid);
        background: var(--acid);
      }
      .index-node.output small { color: #426123; }
      .score-strip {
        display: grid; grid-template-columns: repeat(5,1fr); gap: 9px; margin-top: 16px;
      }
      .score-strip span {
        padding: 10px 8px; border: 1px solid rgba(255,255,255,.07); border-radius: 9px;
        color: #89a0b1; background: rgba(255,255,255,.02);
        font: 700 9px "DM Mono",monospace; text-align: center; letter-spacing: .04em;
      }
      .final-cta {
        display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 40px;
        margin: 0 0 80px; padding: 54px; overflow: hidden; border-radius: 24px;
        border: 1px solid rgba(183,255,92,.24); background:
          radial-gradient(circle at 90% 20%, rgba(183,255,92,.18), transparent 28%),
          linear-gradient(115deg, rgba(10,43,54,.98), rgba(4,15,26,.99));
      }
      .final-cta h2 {
        max-width: 880px; margin: 14px 0 14px; color: #f4f8fb;
        font-size: clamp(38px,4.7vw,65px); line-height: .98; letter-spacing: -.05em;
      }
      .final-cta p { max-width: 760px; margin: 0; color: #9fb1bf; font-size: 18px; line-height: 1.65; }
      .final-cta .landing-cta { min-height: 66px; padding: 0 28px; white-space: nowrap; }
      .signin-page {
        display: grid; place-items: center; padding: 38px 0 18px;
      }
      .signin-copy { width: min(620px, 100%); text-align: center; }
      .signin-copy h1 {
        margin: 13px 0; color: #f2f5f7; font-size: clamp(44px, 6vw, 68px);
        line-height: .94; letter-spacing: -.055em;
      }
      .signin-copy p { margin: 0 auto 18px; max-width: 540px; color: #909ba9; font-size: 18px; line-height: 1.55; }
      .signin-form-head small { color: var(--acid); font: 700 11px "DM Mono",monospace; letter-spacing: .12em; }
      .signin-form-head h2 { margin: 8px 0 3px; font-size: 26px; }
      .signin-form-head p { margin: 0 0 15px; color: #7f8b99; font-size: 15px; }
      .demo-hero { padding: 48px 0 30px; }
      .demo-hero h1 {
        max-width: 940px; margin: 12px 0 16px; color: #f4f7f9;
        font-size: clamp(46px, 6vw, 84px); line-height: .94; letter-spacing: -.055em;
      }
      .demo-hero h1 span { color: #657080; }
      .demo-hero p { max-width: 760px; color: #929eac; font-size: 19px; line-height: 1.65; }
      .source-console {
        margin: 0 0 26px; padding: 22px 24px 12px; border: 1px solid rgba(183,255,92,.16);
        border-radius: 18px; background: linear-gradient(120deg, rgba(183,255,92,.045), #0d1218 36%);
      }
      .source-console h2 { margin: 7px 0 5px; font-size: 26px; }
      .source-console p { margin: 0 0 12px; color: #82909f; font-size: 15px; }
      [data-testid="stTabs"] button { min-height: 48px; font-size: 14px !important; font-weight: 750 !important; }
      [data-testid="stTabs"] button[aria-selected="true"] { color: var(--acid) !important; }
      [data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color: var(--acid) !important; }
      [data-testid="stFileUploader"] { padding: 12px 0; }
      [data-testid="stFileUploader"] section {
        min-height: 120px; border-color: rgba(183,255,92,.18) !important;
        background: #0d1218 !important;
      }
      [data-testid="stFileUploader"] span, [data-testid="stFileUploader"] small {
        font-size: 14px !important;
      }
      .source-status {
        margin: 12px 0 22px; padding: 13px 15px; border-left: 3px solid var(--acid);
        color: #b8c2cc; background: rgba(183,255,92,.045); font-size: 15px;
      }
      .coach-shell {
        margin: 30px 0 6px; padding: 26px; border: 1px solid rgba(183,255,92,.2);
        border-radius: 18px; background:
          radial-gradient(circle at 0 0, rgba(183,255,92,.1), transparent 34%),
          #0d1218;
      }
      .coach-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; }
      .coach-head small { color: var(--acid); font: 600 11px "DM Mono", monospace; letter-spacing: .14em; }
      .coach-head h2 { margin: 8px 0 0; color: #f2f5f7; font-size: 28px; }
      .coach-head span { max-width: 430px; color: #8995a4; font-size: 14px; line-height: 1.55; text-align: right; }
      .coach-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }
      .coach-card { padding: 18px; border: 1px solid rgba(255,255,255,.07); border-radius: 13px; background: rgba(255,255,255,.02); }
      .coach-card label { color: #687585; font: 600 10px "DM Mono", monospace; letter-spacing: .12em; }
      .coach-card strong { display: block; margin: 9px 0; color: #e7ecf0; font-size: 16px; line-height: 1.35; }
      .coach-card p { margin: 0; color: #8b97a5; font-size: 14px; line-height: 1.65; white-space: pre-line; }
      .coach-card.acid { border-color: rgba(183,255,92,.22); background: rgba(183,255,92,.035); }

      /* Demo-room legibility: every secondary label remains readable at distance. */
      .brand-name { font-size: 23px; }
      .brand-name small, .system-state { font-size: 12px; }
      .eyebrow { font-size: 14px; }
      .hero p { max-width: 800px; font-size: 20px; }
      .metric label { font-size: 12px; }
      .metric strong { font-size: 34px; }
      .section-kicker { font-size: 13px; }
      .panel-title { font-size: 20px; }
      .panel-meta { font-size: 12px; }
      .video-title { font-size: 17px; }
      .video-meta, .source-link { font-size: 12px; }
      .scene-time { font-size: 12px; }
      .scene-title { font-size: 16px; }
      .scene-copy { font-size: 14px; }
      .node b { font-size: 14px; }
      .node small, .graph-key { font-size: 11px; }
      .player-copy small, .ask-label, .bounty-copy small { font-size: 12px; }
      .player-copy p, .pipeline-step, .bounty-copy span { font-size: 15px; }
      .bounty-stats label { font-size: 11px; }
      .coach-card label { font-size: 12px; }
      .coach-card strong { font-size: 20px; }
      .coach-card p, .coach-head span { font-size: 16px; }
      .coach-head h2 { font-size: 36px; }
      .answer {
        margin: 12px 0; padding: 18px 20px; color: #cbd2da; font-size: 16px;
        line-height: 1.65; border-left: 2px solid var(--acid); background: rgba(255,255,255,.025);
        white-space: pre-wrap;
      }
      .user-query { color: #919dab; font-size: 15px; margin: 12px 0 6px; }

      .stChatInputContainer, [data-testid="stChatInput"] { border-color: rgba(183,255,92,.18) !important; }
      [data-testid="stChatInput"] {
        background: #0d1218 !important; border-radius: 12px !important;
      }
      [data-testid="stChatInput"] > div {
        background: #0d1218 !important;
        border-color: rgba(183,255,92,.16) !important;
      }
      [data-testid="stChatInput"] textarea {
        color: #e7ebef !important; font-family: "Manrope", sans-serif !important;
      }
      [data-testid="stChatInput"] textarea::placeholder { color: #6f7a89 !important; }
      [data-testid="stTextInput"] input {
        min-height: 54px; color: #e7ebef !important; font-size: 17px !important;
        background: #0d1218 !important; border-color: rgba(183,255,92,.16) !important;
      }
      [data-testid="stTextInput"] input::placeholder { color: #758191 !important; }
      [data-testid="stTextInput"] label { font-size: 16px !important; }
      .stButton button {
        border: 1px solid rgba(183,255,92,.22); color: #b7ff5c;
        min-height: 48px; padding: 0 18px; background: rgba(183,255,92,.07);
        border-radius: 11px; font-size: 14px; font-weight: 750;
      }
      [data-testid="stFormSubmitButton"] button {
        min-height: 52px; border: 1px solid var(--acid) !important;
        color: #0b1007 !important; background: var(--acid) !important;
        border-radius: 10px; font-size: 14px !important; font-weight: 800 !important;
      }
      [data-testid="stFormSubmitButton"] button p { color: #0b1007 !important; }
      [data-testid="stFormSubmitButton"] button:hover {
        border-color: #d5ff9e !important; background: #c7ff80 !important;
      }
      @media (max-width: 850px) {
        .block-container { padding: 1.1rem; }
        .demo-access-button { min-width: 164px; }
        .hero { padding-top: 34px; }
        .metric-row { grid-template-columns: repeat(2, 1fr); }
        .system-state span:last-child { display: none; }
        .coach-grid { grid-template-columns: 1fr; }
        .coach-head { align-items: flex-start; flex-direction: column; }
        .coach-head span { text-align: left; }
        .landing-hero {
          min-height: 720px; margin-top: 18px; padding: 44px 28px;
          align-items: start; background-position: 63% center !important;
        }
        .landing-copy { width: 100%; }
        .landing-copy h1 { font-size: clamp(54px, 14vw, 82px); }
        .landing-copy p { max-width: 92%; font-size: 18px; }
        .landing-note { display:none; }
        .float-card { display:none; }
        .landing-proof { flex-wrap: wrap; }
        .demo-login-band { grid-template-columns: 1fr; }
        .demo-credentials { grid-template-columns: 1fr; }
        .public-feature-grid { grid-template-columns: 1fr; }
        .story-head, .index-layout, .final-cta { grid-template-columns: 1fr; gap: 28px; }
        .story-head { align-items: start; }
        .process-grid { grid-template-columns: 1fr; gap: 1px; }
        .process-step { min-height: auto; }
        .process-step:after { display: none; }
        .index-section { padding: 26px 18px; }
        .character-list { grid-template-columns: 1fr; }
        .index-graph {
          display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
          min-height: auto; padding: 62px 14px 16px;
        }
        .index-wire { display: none; }
        .index-node {
          position: relative; left: auto !important; top: auto !important;
          min-width: 0; transform: none;
        }
        .index-node.source, .index-node.profile, .index-node.output { grid-column: 1 / -1; }
        .score-strip { grid-template-columns: repeat(2,1fr); }
        .final-cta { padding: 32px 26px; }
      }
    </style>
    """
)

st.html(
    f"""
    <style>
      .block-container:has(.landing-hero) {{
        max-width: none;
        padding: 0;
        background: #03101d;
      }}
      .block-container:has(.landing-hero) .spire-nav {{
        position: absolute;
        z-index: 90;
        top: 0;
        left: 0;
        right: 0;
        min-height: 92px;
        padding: 18px max(5vw, 42px);
        background: linear-gradient(180deg, rgba(2,9,18,.9), rgba(2,9,18,.28) 70%, transparent);
      }}
      .block-container:has(.landing-hero) .nav-divider {{
        position: absolute;
        z-index: 91;
        top: 91px;
        left: max(5vw, 42px);
        right: max(5vw, 42px);
        margin: 0;
      }}
      .landing-hero {{
        min-height: 100svh;
        margin: 0;
        padding: 132px max(5vw, 80px) 70px;
        border: 0;
        border-radius: 0;
        background-image:
          linear-gradient(90deg, rgba(2,10,20,.98) 0%, rgba(2,11,22,.9) 34%, rgba(2,11,22,.25) 62%, rgba(2,11,22,.06) 100%),
          url("{HERO_IMAGE_URI}");
        background-size: cover;
        background-position: center right;
        background-repeat: no-repeat;
      }}
      .landing-body {{
        position: relative;
        padding: 36px max(5vw, 80px) 10px;
        background:
          radial-gradient(circle at 12% 9%, rgba(28,131,186,.17), transparent 22%),
          radial-gradient(circle at 88% 38%, rgba(20,99,145,.2), transparent 25%),
          radial-gradient(circle at 44% 74%, rgba(29,116,153,.12), transparent 25%),
          linear-gradient(180deg, #03101d 0%, #051a2b 32%, #03111f 66%, #020b15 100%);
      }}
      .landing-body:before {{
        content: "";
        position: absolute;
        inset: 0;
        pointer-events: none;
        opacity: .32;
        background-image:
          radial-gradient(circle, rgba(163,222,255,.22) 0 1px, transparent 1.7px),
          linear-gradient(115deg, transparent 0 46%, rgba(93,192,240,.04) 46% 48%, transparent 48% 100%);
        background-size: 96px 96px, 520px 520px;
      }}
      .landing-body > * {{ position: relative; z-index: 1; }}
      .brand-mark:before {{
        content: "";
        width: 30px;
        height: 30px;
        background: url("{LOGO_IMAGE_URI}") center / contain no-repeat;
        filter: drop-shadow(1px 2px 1px rgba(255,255,255,.18));
      }}
      @media (max-width: 850px) {{
        .block-container:has(.landing-hero) .spire-nav {{
          min-height: 84px;
          padding: 14px 18px;
        }}
        .block-container:has(.landing-hero) .nav-divider {{
          top: 83px;
          left: 18px;
          right: 18px;
        }}
        .landing-hero {{
          min-height: 100svh;
          margin: 0;
          padding: 116px 22px 44px;
          background-position: 66% center !important;
        }}
        .landing-body {{ padding: 28px 18px 8px; }}
      }}
      @media (max-width: 520px) {{
        .block-container:has(.landing-hero) .brand-name {{ font-size: 16px; }}
        .block-container:has(.landing-hero) .brand-name small {{ display: none; }}
        .block-container:has(.landing-hero) .demo-access-button {{
          min-width: 132px;
          min-height: 48px;
          font-size: 12px;
        }}
      }}
    </style>
    """
)


def render_nav(action_href: str, action_label: str, status: str = "") -> None:
    """Render the shared Puffer navigation."""
    status_markup = ""
    if status:
        status_markup = (
            '<div class="system-state"><span class="pulse"></span>'
            f"<span>{html.escape(status.upper())}</span></div>"
        )
    st.html(
        f"""
        <nav class="spire-nav">
          <div class="brand">
            <div class="brand-mark" aria-label="Puffer AI"></div>
            <div class="brand-name">PUFFER AI<small>FULL-STREAM GROWTH ENGINE</small></div>
          </div>
          <div class="nav-actions">
            {status_markup}
            <a class="demo-access-button" href="{html.escape(action_href)}">{html.escape(action_label)}</a>
          </div>
        </nav>
        <div class="nav-divider"></div>
        """
    )


view = str(st.query_params.get("view", "landing"))

if view == "landing":
    render_nav("?view=signin", "SIGN IN / DEMO")
    st.html(
        """
        <section class="landing-hero">
          <div class="landing-copy">
            <div class="eyebrow">Ocean-deep video intelligence</div>
            <h1>Your next<br>viral moment<br>is <span>already live.</span></h1>
            <p>
              Puffer understands the entire stream, remembers the context behind
              every reaction, and turns the strongest moments into clips people
              cannot help but share.
            </p>
            <div class="landing-cta-row">
              <a class="landing-cta" href="?view=signin">ENTER THE DEMO →</a>
              <span class="landing-note">FULL STREAMS IN · VIRAL MOMENTS OUT</span>
            </div>
          </div>
          <div class="signal-stage" aria-label="Puffer product visualization">
            <div class="float-card one"><small>WHOLE STREAM</small><strong><em>CONTEXT</em> UNDERSTOOD</strong></div>
            <div class="float-card two"><small>BEST MOMENT</small><strong>HOOK + PAYOFF FOUND</strong></div>
            <div class="float-card three"><small>OUTPUT</small><strong><em>CLIP</em> READY</strong></div>
          </div>
        </section>
        <div class="landing-body">
          <div class="landing-proof">
            <span><b>01</b> UNDERSTANDS THE WHOLE STORY</span>
            <span><b>02</b> EXPLAINS WHY A MOMENT WORKS</span>
            <span><b>03</b> HELPS CREATORS REPEAT THE PATTERN</span>
          </div>
          <section class="public-feature-grid">
            <article class="public-feature">
              <small>WATCH EVERYTHING</small>
              <h3>Hours become moments.</h3>
              <p>Puffer reviews the full stream so a great reaction never disappears inside a six-hour VOD.</p>
              <ul>
                <li>Finds scene changes and natural clip boundaries</li>
                <li>Reads speech, visuals, reactions, and audience energy together</li>
                <li>Keeps every observation attached to an exact timestamp</li>
              </ul>
            </article>
            <article class="public-feature">
              <small>REMEMBER CONTEXT</small>
              <h3>The callback still lands.</h3>
              <p>People, topics, inside jokes, and emotional turns stay connected across the entire broadcast.</p>
              <ul>
                <li>Recognizes recurring people, topics, props, and phrases</li>
                <li>Links the setup of a joke to its payoff hours later</li>
                <li>Explains the story a standalone viewer needs to understand</li>
              </ul>
            </article>
            <article class="public-feature">
              <small>CREATE THE NEXT ONE</small>
              <h3>Virality becomes a lesson.</h3>
              <p>Every recommendation includes the reason it works and a version tailored to your personality.</p>
              <ul>
                <li>Scores the hook, emotion, novelty, clarity, and payoff</li>
                <li>Suggests a title, opening frame, edit, and vertical cut</li>
                <li>Turns the winning mechanic into your next content idea</li>
              </ul>
            </article>
          </section>

          <section class="story-section">
            <div class="story-head">
              <div>
                <small>FROM VOD TO OPPORTUNITY</small>
                <h2>Puffer watches the parts humans miss.</h2>
              </div>
              <p>
                A full stream is not treated like one giant file. Puffer builds a
                searchable memory of every scene, then compares moments across the
                complete story to find the reactions, reveals, jokes, and emotional
                turns that can survive outside the original broadcast.
              </p>
            </div>
            <div class="process-grid">
              <article class="process-step">
                <b>01 · INGEST</b>
                <h3>Bring the whole stream.</h3>
                <p>Paste a VOD link, upload a recording, or select a live-stream archive. The source stays intact while Puffer creates a timed scene map.</p>
                <em>FULL CONTEXT PRESERVED</em>
              </article>
              <article class="process-step">
                <b>02 · INDEX</b>
                <h3>Describe every scene.</h3>
                <p>Faces, voices, dialogue, actions, emotions, objects, topics, and audience reactions become searchable characteristics.</p>
                <em>MULTIMODAL MEMORY</em>
              </article>
              <article class="process-step">
                <b>03 · CONNECT</b>
                <h3>Build the story graph.</h3>
                <p>Puffer links repeated people, running jokes, setups, payoffs, and emotional changes—even when they happen hours apart.</p>
                <em>CONTEXT THAT PERSISTS</em>
              </article>
              <article class="process-step">
                <b>04 · RANK</b>
                <h3>Surface what travels.</h3>
                <p>Each candidate receives evidence, a viral score, a recommended edit, and a creator lesson explaining how to repeat the mechanic.</p>
                <em>CLIP + REASON + NEXT MOVE</em>
              </article>
            </div>
          </section>

          <section class="index-section">
            <div class="index-layout">
              <div class="index-copy">
                <small>THE PUFFER VIDEO INDEX</small>
                <h2>Every characteristic becomes context.</h2>
                <p>
                  Puffer turns one long video into a connected index of timestamped
                  scenes. A moment is not ranked because it is loud; it is ranked
                  because its people, words, emotion, setup, and payoff make sense
                  together.
                </p>
                <div class="character-list">
                  <div class="characteristic"><i></i>People &amp; faces</div>
                  <div class="characteristic"><i></i>Voice &amp; dialogue</div>
                  <div class="characteristic"><i></i>Emotion &amp; energy</div>
                  <div class="characteristic"><i></i>Actions &amp; objects</div>
                  <div class="characteristic"><i></i>Topics &amp; lore</div>
                  <div class="characteristic"><i></i>Audience reaction</div>
                  <div class="characteristic"><i></i>Callbacks &amp; reveals</div>
                  <div class="characteristic"><i></i>Hook &amp; payoff</div>
                </div>
              </div>
              <div>
                <div class="index-graph" aria-label="How Puffer indexes video characteristics">
                  <i class="index-wire h wire-a"></i>
                  <i class="index-wire v wire-b"></i>
                  <i class="index-wire h wire-c"></i>
                  <i class="index-wire v wire-d"></i>
                  <i class="index-wire h wire-e"></i>
                  <i class="index-wire v wire-f"></i>
                  <i class="index-wire h wire-g"></i>
                  <div class="index-node source"><b>FULL VOD</b><small>03:28:00 SOURCE</small></div>
                  <div class="index-node s1"><b>Scene 041</b><small>SETUP · 00:34:12</small></div>
                  <div class="index-node s2"><b>Scene 126</b><small>CALLBACK · 01:20:37</small></div>
                  <div class="index-node s3"><b>Scene 244</b><small>PAYOFF · 02:46:08</small></div>
                  <div class="index-node c1"><b>Kai Cenat</b><small>PERSON · RECURRING</small></div>
                  <div class="index-node c2"><b>Surprise</b><small>EMOTION · +82%</small></div>
                  <div class="index-node c3"><b>Award reveal</b><small>EVENT · NOVELTY</small></div>
                  <div class="index-node c4"><b>Running joke</b><small>TOPIC · CALLBACK</small></div>
                  <div class="index-node c5"><b>Room erupts</b><small>AUDIENCE · ENERGY</small></div>
                  <div class="index-node profile"><b>Moment profile</b><small>HOOK + CONTEXT + PAYOFF</small></div>
                  <div class="index-node output"><b>96 / 100</b><small>VIRAL CANDIDATE</small></div>
                </div>
                <div class="score-strip">
                  <span>HOOK 94</span><span>EMOTION 98</span><span>NOVELTY 91</span><span>CLARITY 95</span><span>PAYOFF 97</span>
                </div>
              </div>
            </div>
          </section>

          <section class="final-cta">
            <div>
              <small>STOP SCRUBBING. START PUBLISHING.</small>
              <h2>See a full stream become a ranked, explainable clip list.</h2>
              <p>Enter the working demo, load the Kai Cenat VOD, ask the video a question, and see exactly why Puffer believes each moment can travel.</p>
            </div>
            <a class="landing-cta" href="?view=signin">OPEN THE LIVE DEMO →</a>
          </section>
        </div>
        """
    )
    st.stop()

if view == "signin":
    render_nav("?view=landing", "BACK TO HOME")
    st.html(
        """
        <section class="signin-page">
          <div class="signin-copy">
            <div class="eyebrow">Hackathon demo access</div>
            <h1>Enter the moment engine.</h1>
            <p>
              Use the prefilled presentation account to open the complete Puffer
              workspace. No account is created and nothing is stored.
            </p>
          </div>
        </section>
        """
    )
    sign_left, sign_center, sign_right = st.columns([1, 1.25, 1])
    with sign_center:
        with st.form("demo_signin_form", border=True):
            st.html(
                """
                <div class="signin-form-head">
                  <small>DEMO CREDENTIALS</small>
                  <h2>Sign in to Puffer</h2>
                  <p>Everything below is prefilled for the live presentation.</p>
                </div>
                """
            )
            st.text_input("Demo email", value="demo@puffer.ai")
            st.text_input("Demo password", value="find-the-moment", type="password")
            enter_demo = st.form_submit_button("ENTER LIVE DEMO →", use_container_width=True)
        if enter_demo:
            st.query_params["view"] = "demo"
            st.rerun()
    st.stop()

data, is_live, status_text = load_context_data()
stats = data["stats"]
render_nav("?view=landing", "EXIT DEMO", status_text)

st.html(
    """
    <section class="demo-hero">
      <div class="eyebrow">Signed in · Puffer workspace</div>
      <h1>Bring any full stream. <span>Find what travels.</span></h1>
      <p>
        Paste a VOD URL, upload a video, or search the demo catalog. Puffer keeps
        the source, conversation, viral evidence, and creator lesson in one place.
      </p>
    </section>
    <div class="source-console">
      <div class="eyebrow">Choose a source</div>
      <h2>What should Puffer watch?</h2>
      <p>Use a full-length stream or VOD. The moments are the output—not the input.</p>
    </div>
    """
)

live_videos = [
    video for video in data.get("videos", []) if vod_id_from(video.get("source_url"))
]
default_vod_url = str(DEMO_DATA["videos"][0]["source_url"])
if is_live and live_videos:
    default_vod_url = safe_http_url(live_videos[0].get("source_url")) or default_vod_url

if "active_vod_url" not in st.session_state:
    st.session_state.active_vod_url = default_vod_url
if "creator_dna" not in st.session_state:
    st.session_state.creator_dna = (
        "Direct, high-energy, competitive builder; blunt, funny under pressure, "
        "and comfortable showing messy progress."
    )
st.session_state.setdefault("vod_results", [])
st.session_state.setdefault("source_notice", "")
st.session_state.setdefault("source_error", "")
st.session_state.setdefault("active_vod_title", "")

# The queue cards deep-link with ?vod=<id>, which keeps VOD selection a plain
# link instead of a widget his layout has no room for.
requested_vod = vod_id_from(st.query_params.get("vod"))
if requested_vod and requested_vod != vod_id_from(st.session_state.active_vod_url):
    st.session_state.active_vod_url = f"https://www.twitch.tv/videos/{requested_vod}"
    st.session_state.vod_results = []


def select_vod(vod_url: str, title: str = "") -> None:
    st.session_state.active_vod_url = safe_http_url(vod_url) or default_vod_url
    st.session_state.active_vod_title = title
    st.session_state.source_error = ""
    try:
        st.query_params["vod"] = vod_id_from(vod_url)
    except Exception:
        pass


def handle_source_input(raw: str, *, limit: int = 8) -> None:
    """A VOD link picks that VOD; anything else is treated as a channel.

    Failure is reported as a sentence in his status strip — the Twitch helpers
    raise RuntimeError with human-readable text, so no traceback ever surfaces.
    """
    st.session_state.source_error = ""
    vid = vod_id_from(raw)
    if vid:
        select_vod(f"https://www.twitch.tv/videos/{vid}")
        st.session_state.vod_results = []
        st.session_state.source_notice = (
            f"VOD {vid} selected. Run the analysis to read its chat into the graph."
        )
        return

    try:
        channel = twitch.channel_from_url(raw)
        vods = twitch.list_vods_any(channel, limit=limit)
    except Exception as exc:
        st.session_state.vod_results = []
        st.session_state.source_notice = ""
        st.session_state.source_error = condense(exc, 300) or "Could not read that channel."
        return

    st.session_state.vod_results = vods
    st.session_state.source_notice = (
        f"{len(vods)} recent past broadcasts listed for {channel}."
        if vods
        else f"No past broadcasts listed for {channel} — VODs may be disabled or expired."
    )


def run_analysis(vod_url: str, title: str, with_clips: bool) -> None:
    """Stage 1 (chat → Neo4j) always; stage 2/3 (clips → TwelveLabs) can fail alone."""
    vid = vod_id_from(vod_url)
    if not vid:
        st.session_state.source_error = "That is not a Twitch VOD link, so there is no chat to read."
        return

    clip_error = ""
    with st.status("Reading the chat and scoring the timeline…", expanded=True) as status:
        try:
            analysis = pipeline.analyze_vod(vid, title=title or f"VOD {vid}")
        except Exception as exc:
            status.update(label="Chat analysis failed", state="error")
            st.session_state.source_notice = ""
            st.session_state.source_error = (
                f"Could not analyze VOD {vid}: {condense(exc, 260)}"
            )
            # This path does not rerun (that would discard the status box), so
            # paint the reason where the user is already looking.
            notice(st.session_state.source_error, tone="error")
            return

        peaks = analysis.get("peaks") or []
        dead = analysis.get("dead_spots") or []
        summary = analysis.get("summary") or {}
        st.write(
            f"⚡ {len(peaks)} clip-worthy moments · "
            f"{safe_number(summary.get('dead_time_pct'), '{:.1f}')}% dead air"
            + (" · saved to Neo4j" if analysis.get("persisted") else " · graph write skipped")
        )

        # Chat analysis is already persisted at this point, so a download or
        # TwelveLabs failure must never read as "the analysis failed".
        if with_clips and peaks:
            bar = st.progress(0.0, text="Cutting the peak windows…")
            try:
                st.write("✂️ Cutting the peak windows (server-side crop)…")
                clips = pipeline.clip_moments(
                    vid, peaks,
                    progress=lambda i, total, name: bar.progress(i / max(1, total), text=name),
                )
                st.write("👁 TwelveLabs is watching each clip…")
                pipeline.enrich_clips(
                    vid, clips, title=title or f"VOD {vid}",
                    progress=lambda i, total, name: bar.progress(
                        i / max(1, total), text=f"TwelveLabs · {name}"
                    ),
                )
            except Exception as exc:
                clip_error = condense(exc, 260)
            finally:
                bar.empty()

        if clip_error:
            status.update(label="Timeline saved · clip pass stopped", state="error")
        else:
            status.update(label="Analysis complete", state="complete")

    select_vod(f"https://www.twitch.tv/videos/{vid}", title)
    st.session_state.source_notice = (
        f"{len(peaks)} moments and {len(dead)} dead spots saved for VOD {vid}."
    )
    st.session_state.source_error = (
        f"Timeline is saved in Neo4j, but the clip + TwelveLabs pass stopped: {clip_error}"
        if clip_error
        else ""
    )
    load_context_data.clear()
    load_vod_timeline.clear()
    st.rerun()


url_tab, upload_tab, search_tab = st.tabs(
    ["PASTE VOD URL", "UPLOAD VIDEO", "SEARCH FULL STREAMS"]
)
uploaded_video = None

with url_tab:
    with st.form("vod_url_form"):
        submitted_url = st.text_input(
            "Full-stream URL",
            value=st.session_state.active_vod_url,
            placeholder="https://www.twitch.tv/videos/…  ·  or a channel: twitch.tv/<channel>",
        )
        load_url = st.form_submit_button("LOAD FULL VOD →", use_container_width=True)
    if load_url and submitted_url.strip():
        handle_source_input(submitted_url.strip())

with upload_tab:
    uploaded_video = st.file_uploader(
        "Upload a full stream or video",
        type=["mp4", "mov", "m4v", "webm"],
        help="For the demo, the uploaded file plays locally in the workspace.",
    )

with search_tab:
    with st.form("vod_search_form"):
        search_query = st.text_input(
            "Search full streams",
            placeholder="Twitch channel URL or name — e.g. kaicenat",
        )
        run_search = st.form_submit_button("SEARCH FULL STREAMS →", use_container_width=True)
    if run_search and search_query.strip():
        handle_source_input(search_query.strip())

notice(st.session_state.get("source_notice"))
notice(st.session_state.get("source_error"), tone="error")

if st.session_state.get("vod_results"):
    st.html('<div class="section-kicker">Recent past broadcasts · pick one to analyze</div>')
    for index, vod in enumerate(st.session_state.vod_results[:8]):
        vod_url = str(vod.get("url") or "")
        vod_title = html.escape(condense(vod.get("title") or f"VOD {vod.get('id')}", 90))
        vod_meta = html.escape(
            f"{format_time(vod.get('duration_s') or 0)} · {vod.get('id') or '—'}"
        )
        card_col, pick_col = st.columns([5, 1], gap="small")
        with card_col:
            st.html(
                f"""
                <div class="video-card">
                  <div class="video-top">
                    <div class="video-icon">{index + 1:02d}</div>
                    <div>
                      <div class="video-title">{vod_title}</div>
                      <div class="video-meta">{vod_meta}</div>
                    </div>
                  </div>
                </div>
                """
            )
        with pick_col:
            if st.button("SELECT", key=f"pick_vod_{vod.get('id') or index}",
                         use_container_width=True):
                select_vod(vod_url, str(vod.get("title") or ""))
                st.session_state.vod_results = []
                st.session_state.source_notice = (
                    f"Selected “{condense(vod.get('title'), 70)}”. Run the analysis to read its chat."
                )
                st.rerun()

active_vod_id = vod_id_from(st.session_state.active_vod_url)
run_col, opts_col = st.columns([1, 2], gap="medium")
with run_col:
    start_analysis = st.button(
        "ANALYZE THIS FULL VOD →",
        use_container_width=True,
        disabled=not active_vod_id,
        help="Downloads only the chat, scores the timeline, and writes it to Neo4j.",
    )
with opts_col:
    want_clips = st.checkbox(
        "Also cut the peak clips and let TwelveLabs watch them "
        "(downloads footage — only for streams you have rights to)",
        value=False,
    )
if start_analysis:
    run_analysis(
        st.session_state.active_vod_url,
        st.session_state.get("active_vod_title") or "",
        want_clips,
    )

st.html(
    f"""
    <div class="metric-row">
      <div class="metric"><label>FULL VODS ANALYZED</label><strong>{int(stats.get('videos') or 0):02d}</strong></div>
      <div class="metric"><label>SCENES WATCHED</label><strong>{int(stats.get('scenes') or 0):03d}</strong></div>
      <div class="metric"><label>CONTEXT SIGNALS</label><strong>{int(stats.get('entities') or 0):02d}</strong></div>
      <div class="metric"><label>VIRAL CANDIDATES</label><strong>{int(stats.get('viral_moments') or 0):02d}</strong></div>
    </div>
    """
)

active_vod_url = str(st.session_state.active_vod_url)
twitch_match = re.search(r"twitch\.tv/videos/(\d+)", active_vod_url)
player, explainer = st.columns([1.7, 1], gap="medium")
with player:
    st.html('<div class="section-kicker">Selected full-stream source</div>')
    if uploaded_video is not None:
        st.video(uploaded_video)
    elif twitch_match:
        st.iframe(
            f"https://player.twitch.tv/?video=v{twitch_match.group(1)}&parent=localhost&autoplay=false",
            width="stretch",
            height=520,
        )
    else:
        st.video(active_vod_url)

with explainer:
    st.html(
        """
        <div class="section-kicker">What Puffer is doing</div>
        <div class="player-copy">
          <small>FULL STREAM → VIRAL OPPORTUNITIES</small>
          <h3>Watch the source.<br/>See the best moments rise.</h3>
          <p>
            Puffer watches the whole stream—not just isolated clips—then turns
            hours of footage into a short list of moments built to travel.
          </p>
          <div class="pipeline-step live">01 · Watches every scene, voice, reaction, and on-screen detail</div>
          <div class="pipeline-step">02 · Spots hooks, punchlines, emotional turns, and quotable payoffs</div>
          <div class="pipeline-step">03 · Remembers people, topics, callbacks, and community context</div>
          <div class="pipeline-step">04 · Delivers ranked timestamps and explains why each moment can spread</div>
        </div>
        """
    )

# ---------------------------------------------------------------- scrubber
selected_video_id = f"twitch:{active_vod_id}" if active_vod_id else ""
performance_rows = data.get("performance") or []
selected_row = next(
    (row for row in performance_rows if row.get("video_id") == selected_video_id), {}
)
selected_moments, selected_dead = (
    load_vod_timeline(selected_video_id) if is_live else ([], [])
)
selected_duration = int(
    selected_row.get("duration_s")
    or max((int(m.get("end") or 0) for m in selected_moments), default=0)
    or 0
)

st.html('<div class="section-kicker">Activity scrubber · every marker opens Twitch at that second</div>')
if selected_moments or selected_dead:
    render_scrubber(
        selected_duration or 1,
        selected_moments,
        selected_dead,
        selected_row.get("url") or active_vod_url,
    )
    st.html(
        f"""
        <div class="metric-row">
          <div class="metric"><label>STREAM LENGTH</label>
            <strong>{html.escape(format_time(selected_duration))}</strong></div>
          <div class="metric"><label>CHAT VELOCITY / MIN</label>
            <strong>{html.escape(safe_number(selected_row.get('msgs_per_min')))}</strong></div>
          <div class="metric"><label>DEAD AIR</label>
            <strong>{html.escape(safe_number(selected_row.get('dead_pct'), '{:.1f}'))}%</strong></div>
          <div class="metric"><label>MOMENTS FOUND</label>
            <strong>{len(selected_moments):02d}</strong></div>
        </div>
        """
    )
elif is_live:
    notice(
        "No timeline for this VOD yet — run ANALYZE THIS FULL VOD and the scrubber "
        "fills in with its moments and dead air."
    )
else:
    notice("Connect the graph to scrub this VOD by activity.")

st.html(
    """
    <div class="ask-panel">
      <div class="ask-label">CHAT WITH THIS VIDEO</div>
      <div class="ask-title">Ask Puffer what happened—and what to clip.</div>
    </div>
    """
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state:
    st.session_state.agent = None

def tools_used(result) -> list[str]:
    """Names of the graph/TwelveLabs tools the agent actually called."""
    try:
        metrics = getattr(result, "metrics", None)
        tool_metrics = getattr(metrics, "tool_metrics", None) or {}
        return sorted(str(name) for name in tool_metrics)
    except Exception:
        return []


for message in st.session_state.messages:
    css_class = "user-query" if message["role"] == "user" else "answer"
    prefix = "YOU · " if message["role"] == "user" else ""
    st.html(f'<div class="{css_class}">{prefix}{html.escape(message["content"])}</div>')
    used = message.get("tools") or []
    if used:
        chips = " · ".join(html.escape(str(name)) for name in used)
        st.html(
            f'<div class="scene-time" style="margin:-6px 0 16px 21px">'
            f"GRAPH TOOLS USED · {chips}</div>"
        )

with st.form("puffer_agent_form", clear_on_submit=True):
    prompt = st.text_input(
        "Ask Puffer",
        placeholder="What is the funniest moment? Find the strongest hook. Why would this spread?",
        label_visibility="collapsed",
    )
    submitted = st.form_submit_button("ASK THIS VIDEO →", use_container_width=True)

if submitted and prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    used_tools: list[str] = []
    with st.spinner("Reading the full-stream context…"):
        if not is_live:
            answer = demo_answer(prompt, st.session_state.creator_dna)
        else:
            try:
                if st.session_state.agent is None:
                    st.session_state.agent = build_agent()
                # Tell the agent which VOD is on screen; the id is ours, not
                # third-party text, so nothing untrusted enters the prompt.
                question = prompt
                if selected_video_id:
                    question = (
                        f"The stream currently selected in the workspace is "
                        f"{selected_video_id}. Prefer it when the question says "
                        f'"this video" or "this stream".\n\n{prompt}'
                    )
                result = st.session_state.agent(question)
                answer = str(result)
                used_tools = tools_used(result)
            except Exception as exc:
                answer = (
                    "The context engine is online, but the reasoning agent is "
                    f"unavailable: {condense(exc, 240)}"
                )
    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "tools": used_tools}
    )
    st.rerun()

st.html(
    """
    <div class="coach-head" style="margin-top:34px">
      <div><small>PUFFER COACH · PERSONALITY TRANSFER</small><h2>Don’t copy the creator. Learn the mechanic.</h2></div>
      <span>Puffer turns a successful moment into a format that fits your natural personality.</span>
    </div>
    """
)
profile = st.text_input("Your Creator DNA", key="creator_dna")
playbook = creator_playbook(profile)
st.html(
    f"""
    <div class="coach-shell">
      <div class="coach-grid">
        <div class="coach-card">
          <label>WHAT MADE THE PATTERN TRAVEL</label>
          <strong>{html.escape(playbook['pattern'])}</strong>
          <p>Short setup, unmistakable stakes, a recognizable personality, and an emotional payoff that survives outside the full stream.</p>
        </div>
        <div class="coach-card acid">
          <label>YOUR CREATOR ADVANTAGE</label>
          <strong>{html.escape(playbook['delivery']).title()}</strong>
          <p>{html.escape(playbook['lesson'])}</p>
        </div>
        <div class="coach-card">
          <label>YOUR OPENING HOOK</label>
          <strong>“{html.escape(playbook['opening'])}”</strong>
          <p>Suggested title: {html.escape(playbook['title'])}</p>
        </div>
        <div class="coach-card acid">
          <label>30-SECOND EXECUTION PLAN</label>
          <p>{html.escape(playbook['script'])}</p>
        </div>
      </div>
    </div>
    """
)

left, middle, right = st.columns([0.82, 2.25, 0.95], gap="medium")

with left:
    video_cards = []
    for index, video in enumerate(data["videos"][:5]):
        title = html.escape(condense(video.get("title") or "Untitled video", 70))
        scenes = int(video.get("scenes") or 0)
        count_label = html.escape(str(video.get("count_label") or f"{scenes} SCENES"))
        duration = html.escape(str(video.get("duration") or "—"))
        fill = max(14, min(100, (scenes or int(video.get("moments") or 0)) * 6))
        card_id = vod_id_from(video.get("source_url") or video.get("id"))
        highlight = (
            "border-color:rgba(183,255,92,.32);background:rgba(183,255,92,.045);"
            if card_id and card_id == active_vod_id
            else ""
        )
        body = f"""
              <div class="video-top">
                <div class="video-icon">{index + 1:02d}</div>
                <div>
                  <div class="video-title">{title}</div>
                  <div class="video-meta">{duration} &nbsp;·&nbsp; {count_label}</div>
                </div>
              </div>
              <div class="bar"><i style="width:{fill}%"></i></div>
        """
        if card_id:
            # Selecting a stream stays a plain link, so his layout needs no new widget.
            video_cards.append(
                f'<a class="video-card" href="?view=demo&amp;vod={html.escape(card_id, quote=True)}"'
                f' style="{highlight}display:block;text-decoration:none;color:inherit">'
                f"{body}</a>"
            )
        else:
            video_cards.append(f'<div class="video-card" style="{highlight}">{body}</div>')
    st.html(
        f"""
        <div class="section-kicker">Full-stream source</div>
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">Full-stream queue</div>
            <div class="panel-meta">{len(data['videos'])} ACTIVE</div>
          </div>
          {''.join(video_cards)}
          <a class="source-link" href="{html.escape(active_vod_url)}" target="_blank">
            OPEN SELECTED SOURCE ↗
          </a>
        </div>
        """
    )

with middle:
    st.html('<div class="section-kicker">Why the moment works</div>')
    render_graph(data["entities"])

with right:
    scene_rows = []
    for scene in data["scenes"][:5]:
        stamp = (
            f"{int(scene.get('score') or 0)}% MATCH · "
            f"{format_time(scene.get('start') or 0)} · "
            f"{html.escape(str(scene.get('emotion') or 'MOMENT'))}"
        )
        scene_url = twitch_timestamp_url(scene.get("url"), scene.get("start") or 0)
        if scene_url:
            href = html.escape(scene_url, quote=True)
            stamp = (
                f'<a href="{href}" target="_blank" rel="noopener"'
                f' style="color:inherit;text-decoration:none">{stamp} ↗</a>'
            )
        scene_rows.append(
            f"""
            <div class="scene">
              <div class="scene-time">{stamp}</div>
              <div class="scene-title">{html.escape(str(scene.get('title') or 'Untitled'))}</div>
              <div class="scene-copy">{html.escape(str(scene.get('description') or 'Scene indexed.'))}</div>
            </div>
            """
        )
    st.html(
        f"""
        <div class="section-kicker">Clip opportunities</div>
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">Ranked moments</div>
            <div class="panel-meta">VIRAL SCORE</div>
          </div>
          {''.join(scene_rows)}
        </div>
        """
    )

open_moments = len(data.get("moments") or []) or int(stats.get("viral_moments") or 0)
st.html(
    f"""
    <div class="bounty-panel">
      <div class="bounty-copy">
        <small>COMMUNITY CLIP BOUNTY</small>
        <strong>Creators grow. Clippers share the upside.</strong>
        <span>Verified clippers earn milestone rewards when Puffer-sourced moments perform.</span>
      </div>
      <div class="bounty-stats">
        <div><b>$250</b><label>DEMO POOL</label></div>
        <div><b>{open_moments:02d}</b><label>OPEN MOMENTS</label></div>
        <div><b>4</b><label>CLIPPERS ACTIVE</label></div>
      </div>
    </div>
    """
)
