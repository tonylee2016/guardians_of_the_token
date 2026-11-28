"""Sentence embeddings for the prompt guard.

Uses a quantized all-MiniLM-L6-v2 ONNX model under ``models/``. The files
are too large to commit (~23 MB), so they're downloaded on first use via
``ensure_model()`` — also exposed as ``python -m guardians_of_the_token.embeddings --download``.
Wheel builds should call ``ensure_model()`` as part of the build hook.

The ONNX session and tokenizer are cached at module scope; the hook is a
short-lived process but this keeps repeated imports fast inside the same
interpreter (e.g. tests).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

_MODELS_DIR = Path(__file__).parent / "models"
_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_MAX_SEQ_LEN = 256

# HuggingFace download URLs for the quantized MiniLM ONNX bundle.
_MODEL_FILES = {
    "model.onnx": "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx",
    "tokenizer.json": "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer.json",
    "tokenizer_config.json": "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer_config.json",
    "config.json": "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/config.json",
}


def ensure_model() -> Path:
    """Download the bundled MiniLM files into ``models/`` if missing."""
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in _MODEL_FILES.items():
        path = _MODELS_DIR / name
        if path.exists() and path.stat().st_size > 0:
            continue
        with urllib.request.urlopen(url) as resp, path.open("wb") as out:
            out.write(resp.read())
    return _MODELS_DIR

# Per-model cosine threshold below which a prompt is considered "very unrelated"
# to the topic anchor's centroid. Distributions differ enough between models
# that the default has to be paired with the model. The MiniLM value was
# calibrated by replaying ~160 real prompts from 8 long sessions plus six
# off-topic prompts at each point: 0.12 blocked 6 of 161 real prompts (the old
# guard: 7) and 76% of off-topic ones (the old guard: 59%). The other values predate the centroid scoring.
SIMILARITY_DEFAULTS: dict[str, float] = {
    "all-MiniLM-L6-v2": 0.12,
    "bge-small-en-v1.5": 0.25,
    "nomic-embed-text": 0.40,
    "snowflake-arctic-embed-xs": 0.20,
}


class EmbeddingUnavailable(RuntimeError):
    """Raised when the embedding backend cannot be loaded."""


_session = None
_tokenizer = None


def _load(model_name: str = _DEFAULT_MODEL):
    global _session, _tokenizer
    if _session is not None and _tokenizer is not None:
        return _session, _tokenizer

    model_path = _MODELS_DIR / "model.onnx"
    tokenizer_path = _MODELS_DIR / "tokenizer.json"
    if not model_path.exists() or not tokenizer_path.exists():
        raise EmbeddingUnavailable(
            f"Bundled embedding model not found under {_MODELS_DIR}. "
            "Run `python -m guardians_of_the_token.embeddings --download` to fetch."
        )

    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise EmbeddingUnavailable(
            "onnxruntime and tokenizers are required for the prompt guard. "
            "Install with: pip install onnxruntime tokenizers"
        ) from exc

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    tok.enable_truncation(max_length=_MAX_SEQ_LEN)

    sess = ort.InferenceSession(
        str(model_path), providers=["CPUExecutionProvider"]
    )

    _session = sess
    _tokenizer = tok
    return sess, tok


def embed(texts: Sequence[str], model_name: str = _DEFAULT_MODEL) -> np.ndarray:
    """Return L2-normalized mean-pooled sentence embeddings."""
    sess, tok = _load(model_name)
    encodings = tok.encode_batch(list(texts))
    ids = np.array([e.ids for e in encodings], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    types = np.zeros_like(ids)

    hidden = sess.run(
        None,
        {"input_ids": ids, "attention_mask": mask, "token_type_ids": types},
    )[0]

    mask_f = mask[..., None].astype(np.float32)
    pooled = (hidden * mask_f).sum(axis=1) / np.clip(mask_f.sum(axis=1), 1, None)
    norm = np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled / np.clip(norm, 1e-9, None)


_CACHE_MAX = 512  # embeddings kept between prompts (~1.5 KB each)


def _cache_path(model_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in model_name)
    return Path("~/.guardians").expanduser() / f"embedding-cache-{safe}.npz"


def embed_cached(texts: Sequence[str], model_name: str = _DEFAULT_MODEL) -> np.ndarray:
    """Like embed(), but reuses vectors for texts seen on earlier prompts.

    The prompt guard re-scores the same session chunks on every prompt; only
    the new prompt and the odd new chunk need the model. Any cache error just
    falls back to embedding everything.
    """
    keys = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in texts]
    path = _cache_path(model_name)
    cached: dict = {}
    try:
        with np.load(path) as data:
            cached = dict(zip(data["keys"].tolist(), data["vectors"]))
    except Exception:
        cached = {}
    missing = sorted({k for k in keys if k not in cached})
    if missing:
        index = {k: i for i, k in enumerate(keys)}
        fresh = embed([texts[index[k]] for k in missing], model_name=model_name)
        cached.update(zip(missing, fresh))
        # Keep this call's entries plus the most recent others, newest last.
        order = [k for k in cached if k not in set(keys)] + list(dict.fromkeys(keys))
        order = order[-_CACHE_MAX:]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(tmp, keys=np.array(order), vectors=np.array([cached[k] for k in order]))
            os.replace(tmp, path)
        except Exception:
            pass
    return np.array([cached[k] for k in keys])


def default_similarity_threshold(model_name: Optional[str]) -> float:
    if not model_name:
        return SIMILARITY_DEFAULTS[_DEFAULT_MODEL]
    return SIMILARITY_DEFAULTS.get(model_name, SIMILARITY_DEFAULTS[_DEFAULT_MODEL])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the bundled MiniLM ONNX + tokenizer into models/."
    )
    # Accepted for compatibility with the hint in EmbeddingUnavailable; downloading is the default.
    parser.add_argument("--download", action="store_true", help="Download the model files (default).")
    parser.parse_args()
    path = ensure_model()
    print(f"Model files ready in {path}")


if __name__ == "__main__":
    main()
