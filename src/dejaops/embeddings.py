"""Embeddings, from Amazon Bedrock (Titan V2) or Voyage AI.

All embeddings are L2-normalized to unit length before storage. CockroachDB's
vector index accelerates only L2 distance (<->); on unit vectors, L2 ranking is
monotonically equivalent to cosine ranking, so normalization gives us cosine
semantics on the accelerated path.

Both providers emit 1024-dim vectors, matching the fixed VECTOR(1024) column —
switching providers needs no schema migration, but does need a re-embed
(`scripts/seed.py --wipe-all`): vectors from different embedding spaces must
never share an index, or similarity silently degrades.

FAKE_EMBEDDINGS=1 switches to a deterministic hash-based embedding so the full
stack runs offline (local dev, CI) with stable, repeatable similarity results.
"""

import hashlib
import json
import logging
import math
import struct
import time
from functools import lru_cache

from .config import settings

log = logging.getLogger("dejaops.embeddings")

_MAX_INPUT_CHARS = 20_000  # both providers cap input; truncate defensively
_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_MAX_RETRIES = 8


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _fake_embedding(text: str, dim: int) -> list[float]:
    """Deterministic pseudo-embedding: unit vector derived from content hashes.

    Overlapping token windows hash into the same buckets for similar texts, so
    related seed incidents still rank above unrelated ones — good enough to
    exercise recall end-to-end without a vendor account.
    """
    vec = [0.0] * dim
    tokens = text.lower().split()
    for i in range(len(tokens)):
        for window in (1, 2, 3):
            if i + window > len(tokens):
                continue
            chunk = " ".join(tokens[i : i + window])
            digest = hashlib.sha256(chunk.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = struct.unpack(">H", digest[5:7])[0] / 65535.0
            vec[bucket] += sign * weight
    return l2_normalize(vec)


def _titan_embedding(text: str, cfg) -> list[float]:
    import boto3  # deferred so offline mode never needs it

    client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
    body = json.dumps({"inputText": text, "dimensions": cfg.embed_dim, "normalize": True})
    resp = client.invoke_model(modelId=cfg.resolved_embed_model, body=body)
    return json.loads(resp["body"].read())["embedding"]


def _voyage_embedding(text: str, input_type: str, cfg) -> list[float]:
    """Voyage AI REST call via httpx (already a transitive dep — no new package).

    `input_type` asymmetrically encodes queries and documents, which measurably
    improves retrieval over embedding both the same way.
    """
    import httpx

    if not cfg.voyage_api_key:
        raise RuntimeError("EMBED_PROVIDER=voyage but VOYAGE_API_KEY is not set")
    payload = {
        "input": [text],
        "model": cfg.resolved_embed_model,
        "input_type": input_type,
        "output_dimension": cfg.embed_dim,
    }
    headers = {"Authorization": f"Bearer {cfg.voyage_api_key}"}

    # Voyage's free tier rate-limits hard (a few requests/minute), so 429s are
    # expected rather than exceptional: honor Retry-After and back off.
    for attempt in range(_VOYAGE_MAX_RETRIES):
        resp = httpx.post(_VOYAGE_URL, json=payload, headers=headers, timeout=60.0)
        if resp.status_code == 429:
            wait = float(resp.headers.get("retry-after", 0)) or min(2**attempt * 5, 60)
            log.warning("voyage 429; retrying in %.0fs (%d/%d)", wait, attempt + 1, _VOYAGE_MAX_RETRIES)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    raise RuntimeError("voyage rate limit: exhausted retries")


@lru_cache(maxsize=512)
def embed(text: str, input_type: str = "document") -> tuple[float, ...]:
    """Embed `text`, returning a unit-length vector (tuple for cacheability).

    `input_type` is "document" for stored memory chunks and "query" for recall
    searches; providers without the distinction ignore it.
    """
    cfg = settings()
    text = text[:_MAX_INPUT_CHARS]

    if cfg.fake_embeddings:
        return tuple(_fake_embedding(text, cfg.embed_dim))
    if cfg.embed_provider == "voyage":
        vec = _voyage_embedding(text, input_type, cfg)
    else:
        vec = _titan_embedding(text, cfg)

    # Providers normalize when asked, but normalize defensively: correctness of
    # the L2==cosine equivalence depends on it.
    return tuple(l2_normalize(vec))
