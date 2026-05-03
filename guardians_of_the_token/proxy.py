import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from guardians_of_the_token.guard import check_messages

app = FastAPI(title="Guardians of the Token")

ANTHROPIC_API = "https://api.anthropic.com"
OPENAI_API = "https://api.openai.com"


def _extract_messages(body: dict) -> list:
    return body.get("messages", [])


@app.post("/anthropic/{path:path}")
async def anthropic_proxy(path: str, request: Request):
    body = await request.json()
    guard = check_messages(_extract_messages(body))
    if guard["status"] == "blocked":
        raise HTTPException(status_code=413, detail={
            "error": "Guardians of the Token: request blocked",
            "tokens": guard["tokens"],
            "limit": guard["limit"],
        })
    if guard["status"] == "confirm":
        return JSONResponse(status_code=202, content={
            "guardians_warning": True,
            "message": (
                f"Guardians of the Token: {guard['tokens']:,} tokens detected "
                f"(estimated cost ${guard['cost']}). Resend with confirmed=true to proceed."
            ),
            "tokens": guard["tokens"],
            "cost": guard["cost"],
        })

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ANTHROPIC_API}/{path}",
            headers=headers,
            json=body,
            timeout=120,
        )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/openai/{path:path}")
async def openai_proxy(path: str, request: Request):
    body = await request.json()
    guard = check_messages(_extract_messages(body))
    if guard["status"] == "blocked":
        raise HTTPException(status_code=413, detail={
            "error": "Guardians of the Token: request blocked",
            "tokens": guard["tokens"],
            "limit": guard["limit"],
        })
    if guard["status"] == "confirm":
        return JSONResponse(status_code=202, content={
            "guardians_warning": True,
            "message": (
                f"Guardians of the Token: {guard['tokens']:,} tokens detected "
                f"(estimated cost ${guard['cost']}). Resend with confirmed=true to proceed."
            ),
            "tokens": guard["tokens"],
            "cost": guard["cost"],
        })

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{OPENAI_API}/{path}",
            headers=headers,
            json=body,
            timeout=120,
        )
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Guardians of the Token"}


def main():
    uvicorn.run("guardians_of_the_token.proxy:app", host="127.0.0.1", port=8080)
