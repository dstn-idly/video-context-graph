"""Preflight check. Run this first — it tells you exactly which piece is broken.

    python scripts/check_env.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OK, BAD, SKIP = "  OK  ", " FAIL ", " SKIP "


def line(status, name, detail=""):
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def main():
    failures = 0

    # --- config ---
    try:
        from vcg import config
    except Exception as exc:
        line(BAD, "config import", exc)
        return 1

    for key in ["OPENAI_API_KEY", "TWELVELABS_API_KEY", "NEO4J_URI", "NEO4J_PASSWORD"]:
        if getattr(config, key, ""):
            line(OK, key, "set")
        else:
            line(BAD, key, "missing from .env")
            failures += 1

    if config.TWELVELABS_INDEX_ID:
        line(OK, "TWELVELABS_INDEX_ID", config.TWELVELABS_INDEX_ID)
    else:
        line(SKIP, "TWELVELABS_INDEX_ID", "empty — run scripts/create_index.py")

    # --- OpenAI ---
    try:
        from vcg import clients
        models = clients.openai_client().models.list()
        line(OK, "OpenAI", f"reachable ({len(models.data)} models)")
    except Exception as exc:
        line(BAD, "OpenAI", exc)
        failures += 1

    # --- TwelveLabs ---
    try:
        tl = clients.twelvelabs()
        surfaces = [a for a in ("indexes", "assets", "search", "analyze", "tasks", "generate")
                    if hasattr(tl, a)]
        line(OK, "TwelveLabs", f"client built; surfaces: {', '.join(surfaces)}")
    except Exception as exc:
        line(BAD, "TwelveLabs", exc)
        failures += 1

    # --- Neo4j ---
    try:
        from vcg import graph
        graph.get_driver().verify_connectivity()
        line(OK, "Neo4j", f"connected to {config.NEO4J_URI}")
        line(OK, "Neo4j stats", graph.stats())
    except Exception as exc:
        line(BAD, "Neo4j", exc)
        failures += 1

    # --- Strands ---
    try:
        import strands
        line(OK, "Strands", f"v{getattr(strands, '__version__', 'unknown')}")
    except Exception as exc:
        line(BAD, "Strands", exc)
        failures += 1

    print()
    print("All green — you're ready to build." if not failures else f"{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
