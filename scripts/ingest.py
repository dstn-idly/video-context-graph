"""Ingest a video into the context graph.

    # brand new video from a direct URL to a raw media file
    python scripts/ingest.py --url https://example.com/clip.mp4 --title "Demo clip"

    # local file
    python scripts/ingest.py --path data/clip.mp4 --title "Demo clip"

    # a video already indexed in TwelveLabs
    python scripts/ingest.py --video-id 68f1... --title "Demo clip"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vcg import ingest  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--title", required=True)
    p.add_argument("--url")
    p.add_argument("--path")
    p.add_argument("--video-id")
    args = p.parse_args()

    if args.video_id:
        result = ingest.ingest_video(args.video_id, args.title)
    elif args.url or args.path:
        result = ingest.ingest_from_source(args.title, url=args.url, path=args.path)
    else:
        p.error("pass one of --url, --path, or --video-id")

    print(f"\nDone: {result['scenes']} scenes for {result['title']} ({result['video_id']})")
    print(f"\nSummary:\n{result['summary']}")


if __name__ == "__main__":
    main()
