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
# to the topic anchor. Distributions differ enough between models that the
# default has to be paired with the model. Calibrated empirically against the
# conversation-based anchor — clearly off-topic prompts (e.g. "trip to Tokyo"
# against a coding session) score ~0.03-0.10, while short on-topic prompts
# ("is this good enough", "summarize") score ~0.10-0.20.
SIMILARITY_DEFAULTS: dict[str, float] = {
    "all-MiniLM-L6-v2": 0.10,
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


def cosine_similarity(a: str, b: str, model_name: str = _DEFAULT_MODEL) -> float:
    vectors = embed([a, b], model_name=model_name)
    return float(vectors[0] @ vectors[1])


def default_similarity_threshold(model_name: Optional[str]) -> float:
    if not model_name:
        return SIMILARITY_DEFAULTS[_DEFAULT_MODEL]
    return SIMILARITY_DEFAULTS.get(model_name, SIMILARITY_DEFAULTS[_DEFAULT_MODEL])


def main() -> None:
    parser = argparse.ArgumentParser(description="Embedding model utilities.")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Fetch the bundled MiniLM ONNX + tokenizer into models/.",
    )
    args = parser.parse_args()
    if args.download:
        path = ensure_model()
        print(f"Model files ready in {path}")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
