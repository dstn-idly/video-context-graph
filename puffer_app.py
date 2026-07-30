"""Puffer AI — viral-moment intelligence for full-length video.

Run with:
    streamlit run puffer_app.py

The interface uses real Neo4j data when the backend is available and falls back
to representative demo data so the product can still be presented standalone.
"""

from __future__ import annotations

import html
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

from vcg import config, graph  # noqa: E402
from vcg.agent import build_agent  # noqa: E402


st.set_page_config(
    page_title="Puffer AI · Find the Moment",
    page_icon="🐡",
    layout="wide",
    initial_sidebar_state="collapsed",
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
        "Live mode replaces these representative candidates with TwelveLabs "
        "observations and Neo4j context from the connected VOD."
    )
    if any(word in lowered for word in ("personality", "my format", "for me", "go viral")):
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
        videos = graph.run_cypher(
            """
            MATCH (v:Video)
            OPTIONAL MATCH (v)-[:HAS_SCENE]->(s:Scene)
            RETURN v.title AS title, v.video_id AS id, count(s) AS scenes,
                   max(s.end) AS duration
            ORDER BY scenes DESC
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
        if not videos:
            return DEMO_DATA, False, "Graph connected · waiting for first ingest"

        for video in videos:
            video["duration"] = format_time(video.get("duration") or 0)
        return {
            "stats": stats,
            "videos": videos,
            "entities": entities,
            "scenes": scenes,
        }, True, "Neo4j graph online"
    except Exception as exc:
        return DEMO_DATA, False, f"Demo signal · {type(exc).__name__}"


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


st.html(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap');

      :root {
        --ink: #eef2f6;
        --muted: #7b8796;
        --line: rgba(255, 255, 255, .08);
        --panel: rgba(14, 18, 24, .88);
        --acid: #b7ff5c;
        --acid-soft: rgba(183, 255, 92, .12);
      }

      html, body, [class*="css"] {
        font-family: "Manrope", sans-serif;
        color-scheme: dark !important;
        background: #080b0f !important;
      }
      .stApp {
        color: var(--ink);
        background:
          radial-gradient(circle at 78% -5%, rgba(183,255,92,.08), transparent 25%),
          radial-gradient(circle at 0% 60%, rgba(86,104,128,.08), transparent 28%),
          #080b0f;
      }
      [data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent; }
      #MainMenu, [data-testid="stDeployButton"],
      [data-testid="stAppDeployButton"] { display: none !important; }
      [data-testid="stSidebar"] { display: none; }
      [data-testid="stBottom"], [data-testid="stBottomBlockContainer"],
      [data-testid="stChatInputContainer"] {
        background: #080b0f !important;
      }
      .block-container { position: relative; max-width: 1480px; padding: 1.6rem 2.2rem 3rem; }

      .spire-nav {
        display: flex; align-items: center; justify-content: space-between;
        position: relative; z-index: 80; min-height: 64px; padding: 4px 0;
      }
      .nav-divider { height: 1px; margin: 9px 0 0; background: var(--line); }
      .brand { display: flex; align-items: center; gap: 12px; }
      .brand-mark {
        display: grid; place-items: center; width: 34px; height: 34px;
        position: relative; border-radius: 9px;
        background: linear-gradient(145deg, #dcff9e 0%, #aef04d 48%, #75b824 100%);
        transform: perspective(180px) rotateX(7deg) rotateY(-10deg);
        box-shadow:
          0 6px 0 #537f1d,
          0 10px 18px rgba(0,0,0,.45),
          inset 2px 2px 4px rgba(255,255,255,.55),
          inset -3px -3px 6px rgba(54,93,12,.35),
          0 0 28px rgba(183,255,92,.22);
      }
      .brand-mark:before {
        content: ""; width: 19px; height: 19px; border-radius: 3px;
        background: linear-gradient(145deg, #1a2114, #020302);
        clip-path: polygon(0 0, 100% 0, 0 100%);
        filter: drop-shadow(2px 3px 2px rgba(255,255,255,.16));
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
        min-height: 365px; padding: 24px; border: 1px solid var(--line);
        border-radius: 18px; background: linear-gradient(145deg, rgba(18,24,31,.98), rgba(11,15,20,.98));
      }
      .player-copy small { color: var(--acid); font: 500 8px "DM Mono", monospace; letter-spacing: .15em; }
      .player-copy h3 { margin: 14px 0 10px; color: #edf2f6; font-size: 24px; line-height: 1.15; }
      .player-copy p { color: #7c8896; font-size: 11px; line-height: 1.65; }
      .pipeline-step { margin-top: 16px; padding: 11px 12px; border-left: 2px solid #384451; color: #8d99a8; font-size: 10px; }
      .pipeline-step.live { border-color: var(--acid); color: #d8e0e7; background: rgba(183,255,92,.04); }
      .landing-hero {
        position: relative; display: grid; grid-template-columns: 1.15fr .85fr;
        align-items: center; gap: 56px; min-height: 670px; padding: 62px 0 72px;
        overflow: hidden; border-bottom: 1px solid var(--line);
      }
      .landing-copy { position: relative; z-index: 2; }
      .landing-copy .eyebrow { margin-bottom: 20px; }
      .landing-copy h1 {
        max-width: 820px; margin: 0; color: #f5f7f9;
        font-size: clamp(64px, 7.6vw, 116px); line-height: .88; letter-spacing: -.07em;
      }
      .landing-copy h1 span { color: #5d6878; }
      .landing-copy p {
        max-width: 720px; margin: 30px 0 0; color: #98a4b2;
        font-size: 21px; line-height: 1.65;
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
        position: relative; height: 510px; perspective: 900px;
      }
      .signal-card {
        position: absolute; inset: 34px 26px 44px 10px; overflow: hidden;
        border: 1px solid rgba(183,255,92,.18); border-radius: 28px;
        background:
          radial-gradient(circle at 55% 45%, rgba(183,255,92,.16), transparent 28%),
          linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px),
          linear-gradient(145deg, #11171d, #090c10);
        background-size: auto, 34px 34px, 34px 34px, auto;
        transform: rotateY(-7deg) rotateX(3deg);
        box-shadow: -34px 44px 80px rgba(0,0,0,.55), inset 0 1px rgba(255,255,255,.08);
      }
      .signal-core {
        position: absolute; left: 50%; top: 50%; display: grid; place-content: center;
        width: 128px; height: 128px; transform: translate(-50%,-50%); border-radius: 999px;
        color: #0c1108; text-align: center;
        background: radial-gradient(circle at 35% 28%, #e3ffb2, #a8ed46 52%, #6ea922);
        box-shadow: 0 0 0 32px rgba(183,255,92,.035), 0 0 85px rgba(183,255,92,.22);
      }
      .signal-core b { font-size: 18px; letter-spacing: .12em; }
      .signal-core small { margin-top: 4px; font: 700 8px "DM Mono", monospace; letter-spacing: .12em; }
      .signal-line { position:absolute; left:50%; top:50%; height:1px; width:34%; transform-origin:left; background:linear-gradient(90deg,rgba(183,255,92,.55),transparent); }
      .signal-line.a { transform:rotate(-35deg); }
      .signal-line.b { transform:rotate(28deg); }
      .signal-line.c { transform:rotate(152deg); }
      .float-card {
        position: absolute; z-index: 2; min-width: 172px; padding: 16px 18px;
        border: 1px solid rgba(255,255,255,.1); border-radius: 14px;
        color: #e8edf1; background: rgba(15,20,26,.93);
        box-shadow: 0 18px 40px rgba(0,0,0,.38); backdrop-filter: blur(18px);
      }
      .float-card small { color: #7c8997; font: 600 10px "DM Mono", monospace; letter-spacing: .1em; }
      .float-card strong { display:block; margin-top:7px; font-size:20px; }
      .float-card em { color:var(--acid); font-style:normal; }
      .float-card.one { top:6px; right:0; }
      .float-card.two { left:0; bottom:26px; }
      .float-card.three { right:8px; bottom:0; }
      .landing-proof {
        display:flex; gap:26px; padding:22px 0 34px; color:#717d8b;
        font:600 12px "DM Mono",monospace; letter-spacing:.06em;
      }
      .landing-proof b { color:#dce3e9; font-size:15px; }
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
      [data-testid="stTextInput"] label { font-size: 16px !important; }
      .stButton button {
        border: 1px solid rgba(183,255,92,.22); color: #b7ff5c;
        min-height: 48px; padding: 0 18px; background: rgba(183,255,92,.07);
        border-radius: 11px; font-size: 14px; font-weight: 750;
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
        .landing-hero { grid-template-columns: 1fr; gap: 12px; min-height: auto; padding-top: 42px; }
        .landing-copy h1 { font-size: clamp(56px, 15vw, 86px); }
        .signal-stage { height: 430px; }
        .landing-proof { flex-wrap: wrap; }
        .demo-login-band { grid-template-columns: 1fr; }
        .demo-credentials { grid-template-columns: 1fr; }
      }
    </style>
    """
)


data, is_live, status_text = load_context_data()
stats = data["stats"]

st.html(
    f"""
    <nav class="spire-nav">
      <div class="brand">
        <div class="brand-mark"></div>
        <div class="brand-name">PUFFER AI<small>FULL-STREAM GROWTH ENGINE</small></div>
      </div>
      <div class="nav-actions">
        <div class="system-state">
          <span class="pulse"></span>
          <span>{html.escape(status_text.upper())}</span>
        </div>
        <a class="demo-access-button" href="#demo-access">SIGN IN / DEMO</a>
      </div>
    </nav>
    <div class="nav-divider"></div>
    <section class="landing-hero">
      <div class="landing-copy">
        <div class="eyebrow">Video intelligence for the creator economy</div>
        <h1>Your next viral moment is <span>already live.</span></h1>
        <p>
          Puffer watches the entire stream, understands every reaction, callback,
          and community signal, then turns the strongest moments into clips people
          cannot help but share.
        </p>
        <div class="landing-cta-row">
          <a class="landing-cta" href="#puffer-demo">WATCH THE LIVE DEMO ↓</a>
          <span class="landing-note">FULL VOD · MULTIMODAL · GRAPH-GROUNDED</span>
        </div>
      </div>
      <div class="signal-stage" aria-label="Puffer viral signal visualization">
        <div class="signal-card">
          <i class="signal-line a"></i>
          <i class="signal-line b"></i>
          <i class="signal-line c"></i>
          <div class="signal-core"><b>PUFFER</b><small>VIRAL GRAPH</small></div>
        </div>
        <div class="float-card one"><small>TOP SIGNAL</small><strong><em>96%</em> VIRAL MATCH</strong></div>
        <div class="float-card two"><small>FULL VOD</small><strong>03:28:00 WATCHED</strong></div>
        <div class="float-card three"><small>CLIP BOUNTY</small><strong><em>$250</em> OPEN</strong></div>
      </div>
    </section>
    <section class="demo-login-band" id="demo-access">
      <div class="demo-login-copy">
        <small>HACKATHON DEMO ACCESS</small>
        <h2>Skip the signup. Enter the product.</h2>
        <p>This is a presentation login—no account is created and no credentials are stored.</p>
      </div>
      <div class="demo-credentials">
        <div class="credential"><label>DEMO EMAIL</label><b>demo@puffer.ai</b></div>
        <div class="credential"><label>DEMO PASSWORD</label><b>find-the-moment</b></div>
        <a class="demo-enter" href="#puffer-demo">ENTER DEMO ↓</a>
      </div>
    </section>
    <div class="landing-proof">
      <span><b>01</b> WHOLE-STREAM MEMORY</span>
      <span><b>02</b> EXPLAINABLE VIRAL SIGNALS</span>
      <span><b>03</b> CREATOR + CLIPPER UPSIDE</span>
    </div>
    <div id="puffer-demo"></div>
    <div class="metric-row">
      <div class="metric"><label>FULL VODS ANALYZED</label><strong>{int(stats.get('videos', 0)):02d}</strong></div>
      <div class="metric"><label>SCENES WATCHED</label><strong>{int(stats.get('scenes', 0)):03d}</strong></div>
      <div class="metric"><label>CONTEXT SIGNALS</label><strong>{int(stats.get('entities', 0)):02d}</strong></div>
      <div class="metric"><label>VIRAL CANDIDATES</label><strong>{int(stats.get('viral_moments', 0)):02d}</strong></div>
    </div>
    """
)

player, explainer = st.columns([1.7, 1], gap="medium")
with player:
    st.html('<div class="section-kicker">Full-VOD demo source · Official Twitch player</div>')
    st.iframe(
        "https://player.twitch.tv/?video=v2370838441&parent=localhost&autoplay=false",
        width="stretch",
        height=365,
    )

with explainer:
    st.html(
        """
        <div class="section-kicker">What Puffer is doing</div>
        <div class="player-copy">
          <small>FULL STREAM → VIRAL OPPORTUNITIES</small>
          <h3>Watch the source.<br/>See the graph think.</h3>
          <p>
            This official Twitch player keeps the source in context while Puffer
            analyzes authorized VOD media through the hackathon pipeline.
          </p>
          <div class="pipeline-step live">01 · TwelveLabs watches vision, speech, audio, and text</div>
          <div class="pipeline-step">02 · OpenAI scores hooks and structures moment evidence</div>
          <div class="pipeline-step">03 · Neo4j connects callbacks, people, topics, and reactions</div>
          <div class="pipeline-step">04 · Strands ranks the best timestamped clip opportunities</div>
        </div>
        """
    )

st.html(
    """
    <div class="coach-head" style="margin-top:30px">
      <div><small>PUFFER COACH · PERSONALITY TRANSFER</small><h2>Don’t copy the creator. Learn the mechanic.</h2></div>
      <span>Puffer turns a successful moment into a format that fits your natural personality.</span>
    </div>
    """
)
profile = st.text_input(
    "Your Creator DNA",
    value="Direct, high-energy, competitive builder; blunt, funny under pressure, and comfortable showing messy progress.",
)
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
        title = html.escape(str(video.get("title") or "Untitled video"))
        scenes = int(video.get("scenes") or 0)
        duration = html.escape(str(video.get("duration") or "—"))
        fill = max(14, min(100, scenes * 2))
        video_cards.append(
            f"""
            <div class="video-card">
              <div class="video-top">
                <div class="video-icon">{index + 1:02d}</div>
                <div>
                  <div class="video-title">{title}</div>
                  <div class="video-meta">{duration} &nbsp;·&nbsp; {scenes} SCENES</div>
                </div>
              </div>
              <div class="bar"><i style="width:{fill}%"></i></div>
            </div>
            """
        )
    st.html(
        f"""
        <div class="section-kicker">Full-stream source</div>
        <div class="panel">
          <div class="panel-head">
            <div class="panel-title">Twitch VOD queue</div>
            <div class="panel-meta">{len(data['videos'])} ACTIVE</div>
          </div>
          {''.join(video_cards)}
          <a class="source-link" href="https://www.twitch.tv/videos/2370838441" target="_blank">
            OPEN OFFICIAL TWITCH VOD ↗
          </a>
        </div>
        """
    )

with middle:
    st.html('<div class="section-kicker">Viral context graph</div>')
    render_graph(data["entities"])

with right:
    scene_rows = []
    for scene in data["scenes"][:5]:
        scene_rows.append(
            f"""
            <div class="scene">
              <div class="scene-time">{int(scene.get('score', 0))}% MATCH · {format_time(scene.get('start', 0))} · {html.escape(str(scene.get('emotion', 'MOMENT')))}</div>
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

st.html(
    """
    <div class="bounty-panel">
      <div class="bounty-copy">
        <small>COMMUNITY CLIP BOUNTY</small>
        <strong>Creators grow. Clippers share the upside.</strong>
        <span>Verified clippers earn milestone rewards when Puffer-sourced moments perform.</span>
      </div>
      <div class="bounty-stats">
        <div><b>$250</b><label>DEMO POOL</label></div>
        <div><b>18</b><label>OPEN MOMENTS</label></div>
        <div><b>4</b><label>CLIPPERS ACTIVE</label></div>
      </div>
    </div>
    """
)

st.html(
    """
    <div class="ask-panel">
      <div class="ask-label">PUFFER AGENT</div>
      <div class="ask-title">Ask Puffer what to clip next.</div>
    </div>
    """
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state:
    st.session_state.agent = None

for message in st.session_state.messages:
    css_class = "user-query" if message["role"] == "user" else "answer"
    prefix = "YOU · " if message["role"] == "user" else ""
    st.html(f'<div class="{css_class}">{prefix}{html.escape(message["content"])}</div>')

with st.form("puffer_agent_form", clear_on_submit=True):
    prompt = st.text_input(
        "Ask Puffer",
        placeholder="Find a funny reaction, callback, quotable moment, or breakout clip…",
        label_visibility="collapsed",
    )
    submitted = st.form_submit_button("ASK PUFFER →", use_container_width=True)

if submitted and prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.spinner("Traversing scenes, entities, and source moments…"):
        if not is_live:
            answer = demo_answer(prompt, profile)
        else:
            try:
                if st.session_state.agent is None:
                    st.session_state.agent = build_agent()
                answer = str(st.session_state.agent(prompt))
            except Exception as exc:
                answer = f"The graph is online, but the reasoning agent is unavailable: {exc}"
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()
