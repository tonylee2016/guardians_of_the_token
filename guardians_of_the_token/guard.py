from guardians_of_the_token.utils import count_tokens, estimate_cost

MAX_SAFE = 20_000
WARNING_CAP = 80_000
HARD_CAP = 100_000
TOOL_SOFT_CAP = 8_000


def check_messages(messages: list) -> dict:
    tokens = count_tokens(messages)

    if tokens > HARD_CAP:
        return {"status": "blocked", "tokens": tokens, "limit": HARD_CAP}

    if tokens > MAX_SAFE:
        return {"status": "confirm", "tokens": tokens, "cost": estimate_cost(tokens)}

    return {"status": "ok", "tokens": tokens}


def handle_tool_output(output: str) -> dict:
    tokens = count_tokens(output)
    if tokens > TOOL_SOFT_CAP:
        return {"status": "confirm_tool", "tokens": tokens}
    return {"status": "ok", "data": output}
