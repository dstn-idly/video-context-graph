"""Central config. Reads .env once, fails loudly when a key is missing."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

TWELVELABS_API_KEY = os.getenv("TWELVELABS_API_KEY", "")
TWELVELABS_INDEX_ID = os.getenv("TWELVELABS_INDEX_ID", "")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
# Workshop / SSO accounts hand out TEMPORARY creds — without the session token
# every AWS call fails with InvalidClientTokenId.
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN", "")
AWS_ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID", "")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "")
BEDROCK_PEGASUS_MODEL = os.getenv("BEDROCK_PEGASUS_MODEL", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")

AGENT_BACKEND = os.getenv("AGENT_BACKEND", "openai").lower()


def require(name: str) -> str:
    """Fetch a config value or raise with a hint instead of failing deep in an SDK."""
    value = globals().get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to {ROOT / '.env'} (see .env.example).")
    return value
