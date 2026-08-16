"""Embeddings via Amazon Bedrock (Titan Text Embeddings V2).

All embeddings are L2-normalized to unit length before storage. CockroachDB's
vector index accelerates only L2 distance (<->); on unit vectors, L2 ranking is
monotonically equivalent to cosine ranking, so normalization gives us cosine
semantics on the accelerated path.

FAKE_EMBEDDINGS=1 switches to a deterministic hash-based embedding so the full
stack runs offline (local dev, CI) with stable, repeatable similarity results.
"""

import hashlib
import json
import math
import struct
from functools import lru_cache

from .config import settings

_MAX_INPUT_CHARS = 20_000  # Titan V2 caps input; truncate defensively


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _fake_embedding(text: str, dim: int) -> list[float]:
    """Deterministic pseudo-embedding: unit vector derived from content hashes.

    Overlapping token windows hash into the same buckets for similar texts, so
    related seed incidents still rank above unrelated ones — good enough to
    exercise recall end-to-end without Bedrock.
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


@lru_cache(maxsize=512)
def embed(text: str) -> tuple[float, ...]:
    """Embed `text`, returning a unit-length vector (tuple for cacheability)."""
    cfg = settings()
    text = text[:_MAX_INPUT_CHARS]
    if cfg.fake_embeddings:
        return tuple(_fake_embedding(text, cfg.embed_dim))

    import boto3  # deferred so offline mode never needs it

    client = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
    body = json.dumps({"inputText": text, "dimensions": cfg.embed_dim, "normalize": True})
    resp = client.invoke_model(modelId=cfg.embed_model_id, body=body)
    vec = json.loads(resp["body"].read())["embedding"]
    # Titan normalizes when asked, but normalize defensively: correctness of
    # the L2==cosine equivalence depends on it.
    return tuple(l2_normalize(vec))
