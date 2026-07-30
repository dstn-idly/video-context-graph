"""Pull Twitch VODs or clips into the context graph.

Use this on your own channel, or one that has given you permission — Twitch's
ToS does not allow downloading broadcasts you don't have rights to.

    # see what's available first (no download)
    python scripts/twitch_ingest.py --channel yourname --list

    # graph the 3 most recent past broadcasts
    python scripts/twitch_ingest.py --channel yourname --vods 3

    # graph the 10 most-viewed clips — much faster, better for a demo
    python scripts/twitch_ingest.py --channel yourname --clips 10

    # one specific VOD or clip
    python scripts/twitch_ingest.py --url https://www.twitch.tv/videos/123456789
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vcg import ingest, twitch  # noqa: E402


def show(items, kind):
    if not items:
        print(f"  (no {kind} found)")
        return
    for it in items:
        if kind == "vods":
            secs = twitch.parse_duration(it.get("duration", ""))
            print(f"  {it['url']}\n    {it['title'][:70]}  ·  {secs // 60}m  ·  {it['created_at'][:10]}")
        else:
            print(f"  {it['url']}\n    {it['title'][:70]}  ·  {it.get('duration', '?')}s  ·  {it['view_count']} views")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", help="Twitch login name, e.g. 'shroud'")
    p.add_argument("--url", help="Ingest one specific VOD or clip URL")
    p.add_argument("--vods", type=int, default=0, help="How many recent VODs to ingest")
    p.add_argument("--clips", type=int, default=0, help="How many top clips to ingest")
    p.add_argument("--list", action="store_true", help="List only; download nothing")
    p.add_argument("--segment-minutes", type=int, default=45,
                   help="Chunk size for long VODs (analysis caps at 60)")
    p.add_argument("--max-height", type=int, default=720, help="Download resolution cap")
    p.add_argument("--keep-files", action="store_true", help="Don't delete segment files")
    args = p.parse_args()

    # Cap below 60: ffmpeg cuts on keyframes, so a chunk can run slightly longer
    # than requested. The headroom keeps that drift from breaching the 1h limit.
    if args.segment_minutes > 55:
        p.error("--segment-minutes must be <= 55 (TwelveLabs analysis caps at 1 hour; "
                "segments can overshoot slightly because cuts land on keyframes).")
    if not args.channel and not args.url:
        p.error("pass --channel or --url")

    targets: list[tuple[str, str]] = []  # (url, title)

    if args.url:
        targets.append((args.url, args.url.rstrip("/").split("/")[-1]))
    else:
        user = twitch.get_user(args.channel)
        print(f"Channel: {user['display_name']} (id {user['id']})")

        if args.vods or args.list:
            vods = twitch.list_vods(user["id"], limit=args.vods or 10)
            print(f"\nVODs ({len(vods)}):")
            show(vods, "vods")
            if args.vods:
                targets += [(v["url"], v["title"]) for v in vods[: args.vods]]

        if args.clips or args.list:
            clips = twitch.list_clips(user["id"], limit=args.clips or 10)
            print(f"\nClips ({len(clips)}):")
            show(clips, "clips")
            if args.clips:
                targets += [(c["url"], c["title"]) for c in clips[: args.clips]]

    if args.list or not targets:
        if args.list:
            print("\nListing only. Re-run with --vods N or --clips N to ingest.")
        return

    print(f"\n{'=' * 60}\nIngesting {len(targets)} item(s)\n{'=' * 60}")
    ok, failed = 0, []
    for i, (url, title) in enumerate(targets, start=1):
        print(f"\n[{i}/{len(targets)}] {title}")
        try:
            result = ingest.ingest_twitch(
                url, title,
                segment_seconds=args.segment_minutes * 60,
                max_height=args.max_height,
                keep_files=args.keep_files,
            )
            print(f"  -> {result['scenes']} scenes across {result['segments']} segment(s)")
            ok += 1
        except Exception as exc:
            print(f"  !! failed: {exc}")
            failed.append((title, exc))

    print(f"\n{'=' * 60}\nDone: {ok} ingested, {len(failed)} failed")
    for title, exc in failed:
        print(f"  - {title}: {exc}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:  # config/API problems — show the message, not a traceback
        sys.exit(f"\nError: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
