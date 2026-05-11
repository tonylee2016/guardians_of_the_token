import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from guardians_of_the_token import __version__
from guardians_of_the_token.config import estimate_cost, load_config as load_guardians_config, policy_decision
from guardians_of_the_token.events import log_event
from guardians_of_the_token.estimate import estimate_file, estimate_url, url_head_metadata
from guardians_of_the_token.messages import format_context_block
from guardians_of_the_token.test_support import TEST_URL_SIZES
from guardians_of_the_token.utils import count_tokens

SERVER_INFO = {"name": "guardians-of-the-token", "version": __version__}
PROTOCOL_VERSION = "2024-11-05"

DEFAULT_CONTEXT_WINDOW = 200_000
DEFAULT_WARN_PCT = 20
COMPACT_PCT = 90
MAX_LINES = 500
MAX_MATCHES = 50
CHUNK_SUMMARY_MAX_LINES = 1_000

def got_dir(base_dir: Optional[Path] = None) -> Path:
    override = os.environ.get("GUARDIANS_INBOX")
    if override:
        return Path(override).expanduser().resolve()
    anchor = (base_dir or Path.cwd()).expanduser().resolve()
    return anchor / ".got"


def index_dir(base_dir: Optional[Path] = None) -> Path:
    return got_dir(base_dir) / "index"


def make_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def text_result(text: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def json_text(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def safe_path(raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("path is required")
    return Path(raw_path).expanduser().resolve()


def load_config() -> dict:
    return load_guardians_config()


def configured_context_window(args: dict) -> int:
    config = load_guardians_config(args.get("path") or args.get("project_path") or ".")
    return int(args.get("context_window") or config.get("context_window") or DEFAULT_CONTEXT_WINDOW)


def configured_warn_pct(args: dict) -> int:
    config = load_guardians_config(args.get("path") or args.get("project_path") or ".")
    return int(args.get("warn_threshold_pct") or config.get("warn_threshold_pct") or DEFAULT_WARN_PCT)


def file_fingerprint(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    return {
        "fingerprint": f"sha256:{digest}",
        "mtime_ns": stat.st_mtime_ns,
    }


def source_metadata(path: Path, args: dict) -> dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if not path.is_file():
        raise ValueError(f"{path} is not a regular file")
    context_window = configured_context_window(args)
    warn_pct = configured_warn_pct(args)
    config = load_guardians_config(path)
    estimate = estimate_file(str(path), context_window=context_window, warn_pct=warn_pct)
    policy = policy_decision(str(path), config)
    cost = estimate_cost(estimate["estimated_tokens"], config)
    return {
        "path": str(path),
        "kind": "file",
        "bytes": estimate["bytes"],
        "estimated_tokens": estimate["estimated_tokens"],
        "estimated_cost": cost,
        "context_window": context_window,
        "warn_threshold_pct": warn_pct,
        "risk": estimate["risk"],
        "policy": policy,
        "confidence": estimate["confidence"],
        "method": estimate["method"],
        "signals": estimate["signals"],
        **file_fingerprint(path),
    }


def warning_for(meta: dict) -> str:
    if meta.get("policy") == "whitelisted":
        return "This source is whitelisted by Guardians policy."
    if meta.get("policy") == "ignored":
        return "This source is ignored by Guardians policy."
    if meta["risk"] == "safe":
        return "This source is within the configured Guardians threshold."
    return format_context_block(
        target=meta["path"],
        estimated_tokens=meta["estimated_tokens"],
        context_window=meta["context_window"],
        model_label="Claude Desktop project",
        used_tokens=0,
        kind="file",
        action="read",
        blocked_item="source",
        estimated_cost=meta.get("estimated_cost"),
        critical=meta["risk"] == "critical",
    )


def warning_for_url(meta: dict) -> str:
    if meta["risk"] == "safe":
        return "This URL is within the configured Guardians threshold."
    if meta["risk"] == "unknown":
        return (
            "GOT could not determine this URL's response size from HEAD metadata. "
            "Content-Length was missing, so treat the URL as untrusted until it is "
            "fetched or downloaded through a bounded workflow."
        )
    return format_context_block(
        target=meta["url"],
        estimated_tokens=meta["estimated_tokens"],
        context_window=meta["context_window"],
        model_label="Claude Desktop project",
        used_tokens=0,
        kind="url",
        action="fetch",
        blocked_item="source",
        size_kb=None if meta["risk"] == "critical" else meta["bytes"] // 1024,
        estimated_cost=meta.get("estimated_cost"),
        critical=meta["risk"] == "critical",
    )


def url_index_paths(url: str, storage_root: Optional[Path] = None) -> tuple[Path, Path]:
    parsed = urlparse(url)
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    host = parsed.netloc or "url"
    name = Path(parsed.path).name or "index"
    return (index_dir(storage_root) / f"{digest}.json",)


def url_storage_root(args: dict) -> Path:
    if os.environ.get("GUARDIANS_INBOX"):
        return got_dir().parent
    if args.get("project_path"):
        project_path = safe_path(args["project_path"])
        return project_path if project_path.is_dir() else project_path.parent
    return Path.cwd().resolve()


def url_metadata(url: str, args: dict) -> dict:
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    storage_root = url_storage_root(args)
    context_window = configured_context_window(args)
    warn_pct = configured_warn_pct(args)
    head = url_head_metadata(url)
    config = load_guardians_config(storage_root)
    estimate = estimate_url(
        url,
        context_window=context_window,
        warn_pct=warn_pct,
        content_length=head["content_length"],
        content_type=head.get("content_type"),
    )
    cost = estimate_cost(estimate["estimated_tokens"], config)
    (index_path,) = url_index_paths(url, storage_root)
    return {
        "url": url,
        "kind": "url",
        "bytes": estimate["bytes"],
        "estimated_tokens": estimate["estimated_tokens"],
        "estimated_cost": cost,
        "context_window": context_window,
        "warn_threshold_pct": warn_pct,
        "risk": estimate["risk"],
        "confidence": estimate["confidence"],
        "method": estimate["method"],
        "signals": estimate["signals"],
        "content_type": head.get("content_type"),
        "storage_root": str(storage_root),
        "index_path": str(index_path),
    }


def project_policy_text() -> str:
    return """# Guardians of the Token Project Policy

Use GOT only as a preflight check for unknown, new, or potentially large
sources. Do not route ordinary small-file work through GOT after a source
has been classified as `safe`.

Before reading, summarizing, uploading, or analyzing a local file that is new
to this conversation or may be large, call `got_file_size`.

After preflight, inspect the returned risk:
- If risk is `safe`, proceed normally with native tools. Do not keep using
  GOT bounded tools for that source unless the user explicitly asks.
- If risk is `warning` or `critical`, do not request or ingest the full raw file.
- Show the Guardians warning to the user.
- Offer bounded options: inspect beginning, inspect end, search for a term,
  summarize a bounded section, or explicit bypass.

If the user mentions a new path, file, log, transcript, export, PDF, CSV,
JSON, or URL, treat it as untrusted until GOT MCP has checked its size.

For sources with risk `warning` or `critical`, use GOT MCP tools for
bounded file access after the user chooses an option:
- `got_file_head`
- `got_file_tail`
- `got_file_search`
- `got_file_chunk_summary`

Before fetching, summarizing, or analyzing an unknown, new, or potentially large
URL, call `got_url_size`. If risk is `safe`, proceed normally. If risk
is `warning`, `critical`, or `unknown`, show the warning or uncertainty to the
user and wait for their choice before fetching the full URL.
"""


def got_project_init(args: dict) -> dict:
    path = safe_path(args.get("path") or ".")
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.is_file():
        project_dir = path.parent
    else:
        project_dir = path
    storage = got_dir(project_dir)
    policy_path = storage / "GUARDIANS_PROJECT_POLICY.md"
    config_path = project_dir / ".guardians.toml"
    claude_path = project_dir / "CLAUDE.md"
    (storage / "index").mkdir(parents=True, exist_ok=True)
    policy = project_policy_text()
    policy_path.write_text(policy)
    if not config_path.exists():
        config_path.write_text(
            "warn_threshold_pct = 20\n"
            "max_output_tokens = 8000\n"
            "default_input_price_per_million = 3.0\n\n"
            "whitelist = [\"README.md\", \"docs/**\"]\n"
            "ignore = [\"node_modules/**\", \".git/**\", \"dist/**\", \"build/**\"]\n"
        )
    append_claude_policy(claude_path, policy)
    return {
        "project_dir": str(project_dir),
        "got_dir": str(storage),
        "claude_path": str(claude_path),
        "config_path": str(config_path),
        "policy_path": str(policy_path),
        "index_dir": str(storage / "index"),
        "next_steps": [
            "Review the GOT policy appended to CLAUDE.md.",
            "Keep GOT MCP enabled for this project.",
            "Run got_file_size before analyzing unknown or potentially large local files.",
            "Review .guardians.toml if you want project-level thresholds, cost estimates, whitelist, or ignore rules.",
        ],
        "policy": policy,
    }


def append_claude_policy(path: Path, policy: str) -> None:
    marker_start = "<!-- guardians-of-the-token:start -->"
    marker_end = "<!-- guardians-of-the-token:end -->"
    block = f"{marker_start}\n{policy.rstrip()}\n{marker_end}\n"
    existing = path.read_text() if path.exists() else ""
    if marker_start in existing and marker_end in existing:
        before, rest = existing.split(marker_start, 1)
        _, after = rest.split(marker_end, 1)
        path.write_text(f"{before}{block}{after.lstrip(chr(10))}")
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{separator}{block}")


def write_url_index(meta: dict) -> dict:
    storage_root = Path(meta["storage_root"])
    index_dir(storage_root).mkdir(parents=True, exist_ok=True)
    cleaned = dict(meta)
    cleaned.pop("storage_root", None)
    index_path = Path(cleaned["index_path"])
    index_path.write_text(json.dumps(cleaned, indent=2, sort_keys=True) + "\n")
    return cleaned


def got_file_size(args: dict) -> dict:
    path = safe_path(args.get("path", ""))
    meta = source_metadata(path, args)
    meta["warning"] = warning_for(meta)
    if meta["risk"] in {"warning", "critical"} and meta.get("policy") == "guarded":
        log_event(
            {
                "client": "mcp",
                "kind": "file",
                "target": meta["path"],
                "action": "preflight_warning",
                "estimated_tokens": meta["estimated_tokens"],
                "estimated_cost": meta["estimated_cost"],
                "risk": meta["risk"],
            },
            config=load_guardians_config(path),
            base_dir=path,
        )
    return meta


def read_url_index(url: str, args: dict) -> Optional[dict]:
    (path,) = url_index_paths(url, url_storage_root(args))
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def got_url_size(args: dict) -> dict:
    url = args.get("url", "")
    indexed = read_url_index(url, args)
    if indexed is not None:
        indexed["cached"] = True
        indexed["warning"] = warning_for_url(indexed)
        return indexed
    meta = write_url_index(url_metadata(url, args))
    meta["cached"] = False
    meta["warning"] = warning_for_url(meta)
    if meta["risk"] in {"warning", "critical"}:
        log_event(
            {
                "client": "mcp",
                "kind": "url",
                "target": meta["url"],
                "action": "preflight_warning",
                "estimated_tokens": meta["estimated_tokens"],
                "estimated_cost": meta["estimated_cost"],
                "risk": meta["risk"],
            },
            config=load_guardians_config(meta.get("index_path")),
            base_dir=meta.get("index_path"),
        )
    return meta


def clamp(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def got_file_head(args: dict) -> str:
    path = safe_path(args.get("path", ""))
    lines = clamp(args.get("lines"), 100, MAX_LINES)
    output = []
    with path.open(errors="replace") as f:
        for line_number, line in enumerate(f, 1):
            if line_number > lines:
                break
            output.append(f"{line_number}\t{line.rstrip()}")
    return "\n".join(output)


def got_file_tail(args: dict) -> str:
    path = safe_path(args.get("path", ""))
    lines = clamp(args.get("lines"), 100, MAX_LINES)
    total = 0
    tail = deque(maxlen=lines)
    with path.open(errors="replace") as f:
        for total, line in enumerate(f, 1):
            tail.append(line)
    start = max(1, total - len(tail) + 1)
    return "\n".join(
        f"{line_number}\t{line.rstrip()}"
        for line_number, line in enumerate(tail, start)
    )


def got_file_search(args: dict) -> str:
    path = safe_path(args.get("path", ""))
    query = args.get("query", "")
    if not query:
        raise ValueError("query is required")
    max_matches = clamp(args.get("max_matches"), 20, MAX_MATCHES)
    matches = []
    with path.open(errors="replace") as f:
        for line_number, line in enumerate(f, 1):
            if query.lower() in line.lower():
                matches.append(f"{line_number}\t{line.rstrip()}")
                if len(matches) >= max_matches:
                    break
    return "\n".join(matches) if matches else "No matches."


def got_file_chunk_summary(args: dict) -> str:
    path = safe_path(args.get("path", ""))
    start_line = clamp(args.get("start_line"), 1, 10**9)
    line_count = clamp(args.get("line_count"), 100, CHUNK_SUMMARY_MAX_LINES)
    selected = []
    with path.open(errors="replace") as f:
        for line_number, line in enumerate(f, 1):
            if line_number < start_line:
                continue
            if line_number >= start_line + line_count:
                break
            selected.append(line.rstrip())
    text = "\n".join(selected)
    return (
        f"Chunk: {path}:{start_line}-{start_line + max(0, len(selected) - 1)}\n"
        f"Lines: {len(selected):,}\n"
        f"Estimated tokens: ~{count_tokens(text):,}\n\n"
        "Bounded chunk content for Claude to summarize:\n"
        f"{text}"
    )


TOOLS = {
    "got_project_init": {
        "description": "Initialize .got storage and write GUARDIANS_PROJECT_POLICY.md for a Claude Project.",
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Project folder path. Defaults to current directory."}},
        },
        "handler": got_project_init,
    },
    "got_project_policy": {
        "description": "Return project instructions that make Claude call Guardians before reading local files.",
        "schema": {"type": "object", "properties": {}},
        "handler": lambda args: project_policy_text(),
    },
    "got_file_size": {
        "description": (
            "Preflight an unknown, new, or potentially large local file. "
            "Uses file metadata only and returns size, token estimate, risk, and warning. "
            "If risk is safe, proceed normally with native tools."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "context_window": {"type": "integer"},
                "warn_threshold_pct": {"type": "integer"},
            },
            "required": ["path"],
        },
        "handler": got_file_size,
    },
    "got_url_size": {
        "description": (
            "Preflight an unknown, new, or potentially large URL using cached metadata, "
            "fixture metadata, or a real HEAD request. Returns Content-Length, token "
            "estimate, risk, and warning. If risk is safe, proceed normally."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "context_window": {"type": "integer"},
                "warn_threshold_pct": {"type": "integer"},
                "project_path": {"type": "string"},
            },
            "required": ["url"],
        },
        "handler": got_url_size,
    },
    "got_file_head": {
        "description": (
            "For warning/critical sources only after the user chooses inspect beginning: "
            "return the first N lines with line numbers."
        ),
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "lines": {"type": "integer"}},
            "required": ["path"],
        },
        "handler": got_file_head,
    },
    "got_file_tail": {
        "description": (
            "For warning/critical sources only after the user chooses inspect end: "
            "return the last N lines with line numbers."
        ),
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "lines": {"type": "integer"}},
            "required": ["path"],
        },
        "handler": got_file_tail,
    },
    "got_file_search": {
        "description": (
            "For warning/critical sources only after the user chooses search: "
            "return bounded matching lines with line numbers."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "query": {"type": "string"},
                "max_matches": {"type": "integer"},
            },
            "required": ["path", "query"],
        },
        "handler": got_file_search,
    },
    "got_file_chunk_summary": {
        "description": (
            "For warning/critical sources only after the user chooses bounded summary: "
            "return a bounded chunk with metadata so Claude can summarize only that section."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "line_count": {"type": "integer"},
            },
            "required": ["path"],
        },
        "handler": got_file_chunk_summary,
    },
}


def tools_list() -> dict:
    return {
        "tools": [
            {
                "name": name,
                "description": tool["description"],
                "inputSchema": tool["schema"],
            }
            for name, tool in TOOLS.items()
        ]
    }


def dispatch(req: dict) -> Optional[dict]:
    method = req.get("method", "")
    req_id = req.get("id")

    if method == "initialize":
        return make_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return make_response(req_id, {})

    if method == "tools/list":
        return make_response(req_id, tools_list())

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        tool = TOOLS.get(name)
        if tool is None:
            return make_error(req_id, -32602, f"Unknown tool: {name}")
        args = params.get("arguments", {})
        output = tool["handler"](args)
        text = output if isinstance(output, str) else json_text(output)
        return make_response(req_id, text_result(text))

    return make_error(req_id, -32601, f"Method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = dispatch(req)
        except json.JSONDecodeError as e:
            resp = make_error(None, -32700, f"Parse error: {e}")
        except Exception as e:
            resp = make_error(req.get("id") if "req" in dir() else None, -32603, str(e))

        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
