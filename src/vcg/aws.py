"""AWS layer: S3 for footage, Bedrock for the agent brain and Pegasus.

Two distinct ways AWS carries real weight here, both optional:

1. **Bedrock as the agent model** — flip AGENT_BACKEND=bedrock and the Strands
   agent runs on Bedrock instead of OpenAI. Same tools, same graph.

2. **TwelveLabs Pegasus *through* Bedrock** — Pegasus is available as a Bedrock
   model, reading video straight out of S3. That path needs no TwelveLabs API
   key at all: the video understanding is billed through AWS.

S3 also solves a real ingestion problem. TwelveLabs can fetch a *public direct*
media URL server-side, but Twitch serves HLS and our clips are local files. A
presigned S3 URL turns any local clip into something TwelveLabs can pull without
us uploading it through our own process.
"""
import json
import logging

from . import config

log = logging.getLogger(__name__)

# Bedrock inference profiles are region-prefixed. A bare model id like
# "anthropic.claude-..." is rejected in most regions now — "us.anthropic..."
# is the on-demand profile. Overridable because the workshop account may only
# have specific models enabled.
DEFAULT_AGENT_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_PEGASUS_MODEL = "us.twelvelabs.pegasus-1-2-v1:0"


def session():
    """boto3 session from the standard chain (.env vars, ~/.aws, or IAM role)."""
    import boto3

    return boto3.Session(
        aws_access_key_id=config.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY or None,
        aws_session_token=config.AWS_SESSION_TOKEN or None,
        region_name=config.AWS_REGION or None,
    )


def whoami() -> dict:
    """Who are we, per STS. The fastest proof credentials actually work."""
    sts = session().client("sts")
    ident = sts.get_caller_identity()
    return {"account": ident["Account"], "arn": ident["Arn"]}


# --------------------------------------------------------------------------
# S3
# --------------------------------------------------------------------------

def upload_clip(local_path: str, key: str, *, bucket: str = "") -> str:
    """Put a local clip in S3. Returns the s3:// URI."""
    bucket = bucket or config.require("S3_BUCKET")
    session().client("s3").upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"


def presigned_url(key: str, *, bucket: str = "", expires: int = 3600) -> str:
    """Time-limited HTTPS URL for a private object.

    This is what lets TwelveLabs fetch a clip server-side: it needs a direct,
    publicly-resolvable media URL, and a presigned link is exactly that without
    making the bucket public.
    """
    bucket = bucket or config.require("S3_BUCKET")
    return session().client("s3").generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires
    )


# --------------------------------------------------------------------------
# Bedrock
# --------------------------------------------------------------------------

def list_models(prefix: str = "") -> list[str]:
    """Model ids this account can actually invoke — the fastest way to see
    whether Bedrock access has been granted for the models we want."""
    try:
        client = session().client("bedrock")
        ids = [m["modelId"] for m in client.list_foundation_models().get("modelSummaries", [])]
        return sorted(i for i in ids if prefix in i)
    except Exception as exc:
        log.warning("could not list Bedrock models: %s", exc)
        return []


def analyze_video_s3(s3_uri: str, prompt: str, *, model_id: str = "",
                     account_id: str = "") -> str:
    """Run TwelveLabs Pegasus on an S3 video **via Bedrock**.

    A drop-in alternative to clients.analyze() that bills through AWS instead of
    a TwelveLabs API key. Input caps here are Bedrock's, not the TwelveLabs
    API's: 1 hour of video and 2 GB per object.
    """
    model_id = model_id or config.BEDROCK_PEGASUS_MODEL or DEFAULT_PEGASUS_MODEL
    account_id = account_id or config.AWS_ACCOUNT_ID or whoami()["account"]

    body = json.dumps({
        "inputPrompt": prompt,
        "mediaSource": {"s3Location": {"uri": s3_uri, "bucketOwner": account_id}},
    })
    response = session().client("bedrock-runtime").invoke_model(
        body=body, modelId=model_id,
        contentType="application/json", accept="application/json",
    )
    payload = json.loads(response["body"].read())
    return payload.get("message") or payload.get("outputText") or json.dumps(payload)
