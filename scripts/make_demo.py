"""Generate a synthetic chat export so the UI can be demoed with no network.

    python scripts/make_demo.py
    # then paste 999000111 into the app's "VOD URL directly" box

analyze_vod() caches chat at clips/<id>/chat.json and reuses it, so writing a
file there makes the whole timeline work offline. Useful insurance for a live
demo on venue wifi.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

DEMO_ID = "999000111"
DURATION = 3 * 3600  # 3 hour stream

FILLER = ["hi chat", "lets go", "gg", "what game is this", "first time here",
          "o7", "yo", "hey", "nice", "sheesh", "true", "fr"]

SCRIPTED = [
    (1140, "funny", 90, ["KEKW", "LULW", "OMEGALUL", "im dead lmao", "PepeLaugh", "not the chair"]),
    (2400, "hype", 130, ["POGGERS", "LETSGO", "clip it", "INSANE", "no way", "GIGACHAD", "W"]),
    (4020, "awkward", 55, ["yikes", "monkaS", "oof", "awkward", "D:", "oh no"]),
    (5460, "tense", 70, ["PauseChamp", "monkaS", "clutch", "so close", "Copium"]),
    (7800, "funny", 110, ["KEKW", "ICANT", "LULW", "crying", "clip that", "OMEGALUL"]),
    (9300, "hype", 95, ["POG", "LETSGO", "insane", "clip it", "HYPERS"]),
]

# Quiet stretches: menu/loading/AFK — these become the "tighten this up" spots.
DEAD_ZONES = [(300, 780), (5900, 6500), (8400, 8900)]


def main():
    random.seed(11)
    comments = []

    def add(offset, body, user):
        comments.append({
            "content_offset_seconds": float(offset),
            "commenter": {"display_name": user, "name": user.lower()},
            "message": {"body": body, "fragments": [{"text": body}], "bits_spent": 0},
        })

    for second in range(DURATION):
        if any(lo <= second < hi for lo, hi in DEAD_ZONES):
            continue  # dead air
        if random.random() < 0.55:
            add(second, random.choice(FILLER), f"viewer{random.randint(1, 300)}")

    for offset, _kind, volume, vocab in SCRIPTED:
        for i in range(volume):
            add(offset + random.randint(0, 6), random.choice(vocab), f"hype{i}")

    comments.sort(key=lambda c: c["content_offset_seconds"])

    out = Path(__file__).resolve().parents[1] / "clips" / DEMO_ID / "chat.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "streamer": {"name": "demo_streamer", "id": 1},
        "video": {"title": "Demo stream — 3h", "id": DEMO_ID, "length": DURATION},
        "comments": comments,
    }))

    print(f"Wrote {len(comments):,} messages to {out}")
    print(f"\nIn the app, paste this into 'Or paste a VOD URL directly':\n\n    {DEMO_ID}\n")
    print("Clip cutting won't work for the demo id (no real video) — the")
    print("timeline, moments, and coaching view all will.")


if __name__ == "__main__":
    main()
