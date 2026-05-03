def count_tokens(messages):
    if isinstance(messages, str):
        text = messages
    else:
        text = "".join([str(m) for m in messages])
    return int(len(text) / 4)


def estimate_cost(tokens):
    return round(tokens / 1_000_000 * 3, 4)
