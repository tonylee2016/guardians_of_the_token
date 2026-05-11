from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from guardians_of_the_token.events import read_events


def report_data(base_dir: str | Path = ".") -> dict[str, Any]:
    events = read_events(base_dir)
    saved_events = [event for event in events if event.get("action") in {"blocked", "suppressed"}]
    tokens_saved = sum(int(event.get("estimated_tokens") or 0) for event in saved_events)
    cost_saved = sum(float(event.get("estimated_cost") or 0) for event in saved_events)
    by_client = Counter(str(event.get("client") or "unknown") for event in saved_events)
    by_kind = Counter(str(event.get("kind") or "unknown") for event in saved_events)
    top_targets = Counter()
    for event in saved_events:
        target = str(event.get("target") or "(unknown)")
        top_targets[target] += int(event.get("estimated_tokens") or 0)
    return {
        "events": events,
        "blocked_operations": len(saved_events),
        "tokens_saved": tokens_saved,
        "cost_saved": round(cost_saved, 4),
        "by_client": dict(by_client),
        "by_kind": dict(by_kind),
        "top_targets": top_targets.most_common(10),
    }


def format_report(data: dict[str, Any]) -> str:
    lines = [
        "Guardians report",
        "",
        f"Blocked/suppressed operations: {data['blocked_operations']:,}",
        f"Estimated tokens saved: {data['tokens_saved']:,}",
        f"Estimated cost avoided: ${data['cost_saved']:.4f}",
        "",
        "By client:",
    ]
    if data["by_client"]:
        lines.extend(f"- {name}: {count:,}" for name, count in sorted(data["by_client"].items()))
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Top risky targets:")
    if data["top_targets"]:
        lines.extend(f"- {target}: ~{tokens:,} tokens" for target, tokens in data["top_targets"])
    else:
        lines.append("- none")
    return "\n".join(lines)
