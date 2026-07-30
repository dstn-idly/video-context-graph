"""Create a TwelveLabs index once, then paste the id into .env.

    python scripts/create_index.py "hackathon-index"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vcg import clients  # noqa: E402


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "video-context-graph"
    index_id = clients.create_index(name)
    print(f"\nCreated index '{name}'")
    print(f"\n  TWELVELABS_INDEX_ID={index_id}\n")
    print("Add that line to your .env (and tell your teammate).")


if __name__ == "__main__":
    main()
