"""Verify the AWS side end to end.

    python scripts/check_aws.py

Tells you exactly which piece is missing rather than failing deep inside boto3.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OK, BAD, SKIP = "  OK  ", " FAIL ", " SKIP "


def line(status, name, detail=""):
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def main():
    from vcg import aws, config

    failures = 0

    if not config.AWS_ACCESS_KEY_ID:
        line(SKIP, "AWS creds", "not in .env — using ~/.aws or instance role if present")
    else:
        line(OK, "AWS_ACCESS_KEY_ID", config.AWS_ACCESS_KEY_ID[:8] + "…")
        # Workshop creds are temporary and ALWAYS need the session token.
        if config.AWS_ACCESS_KEY_ID.startswith("ASIA") and not config.AWS_SESSION_TOKEN:
            line(BAD, "AWS_SESSION_TOKEN",
                 "missing — an ASIA… key is temporary and will not authenticate without it")
            failures += 1

    # --- identity ---
    try:
        who = aws.whoami()
        line(OK, "STS identity", f"account {who['account']}")
        line(OK, "Region", config.AWS_REGION)
    except Exception as exc:
        line(BAD, "STS identity", str(exc)[:140])
        print("\nGet credentials: AWS Workshop Studio → your event → "
              "'Get AWS CLI credentials' → copy all three values into .env")
        return 1

    # --- bedrock ---
    claude = aws.list_models("anthropic.claude")
    pegasus = aws.list_models("twelvelabs")
    if claude:
        line(OK, "Bedrock · Claude", f"{len(claude)} model(s), e.g. {claude[0]}")
    else:
        line(SKIP, "Bedrock · Claude", "none listed — request access in the Bedrock console")
    if pegasus:
        line(OK, "Bedrock · TwelveLabs Pegasus", ", ".join(pegasus[:2]))
    else:
        line(SKIP, "Bedrock · TwelveLabs Pegasus", "not enabled in this account/region")

    # --- can we actually invoke the agent model? ---
    if config.AGENT_BACKEND == "bedrock":
        try:
            from vcg.agent import build_agent
            build_agent()
            line(OK, "Strands agent", f"built on Bedrock ({config.BEDROCK_MODEL_ID or aws.DEFAULT_AGENT_MODEL})")
        except Exception as exc:
            line(BAD, "Strands agent", str(exc)[:140])
            failures += 1
    else:
        line(SKIP, "Strands agent", "AGENT_BACKEND=openai — set to 'bedrock' to run on AWS")

    # --- s3 ---
    if config.S3_BUCKET:
        try:
            aws.session().client("s3").head_bucket(Bucket=config.S3_BUCKET)
            line(OK, "S3", f"{config.S3_BUCKET} reachable")
        except Exception as exc:
            line(BAD, "S3", str(exc)[:120])
            failures += 1
    else:
        line(SKIP, "S3_BUCKET", "unset — only needed for Pegasus-via-Bedrock ingest")

    print()
    print("AWS is wired up." if not failures else f"{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ImportError as exc:
        raise SystemExit(f"Missing dependency: {exc}")
