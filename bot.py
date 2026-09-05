"""magicpin AI Challenge — Vera-replacement bot.

FastAPI server implementing the 5 endpoints from challenge-testing-brief.md:
/v1/context, /v1/tick, /v1/reply, /v1/healthz, /v1/metadata (+ optional /v1/teardown).

Run: uvicorn bot:app --host 0.0.0.0 --port 8080
"""
import itertools
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import composer
import conversation_handlers as ch

app = FastAPI()
START = time.time()
SUBMITTED_AT = "2026-04-26T08:00:00Z"
VALID_SCOPES = {"category", "merchant", "customer", "trigger"}

# In-memory stores. Fine per the brief ("Storing in memory is fine; just
# don't restart between calls") — the judge issues /v1/teardown at test end.
contexts: dict[tuple[str, str], dict] = {}      # (scope, context_id) -> {version, payload}
conversations: dict[str, dict] = {}             # conversation_id -> {state, merchant_id, customer_id}
sent_suppression_keys: set[str] = set()
_conv_seq = itertools.count(1)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_payload(scope: str, context_id: Optional[str]):
    if not context_id:
        return None
    entry = contexts.get((scope, context_id))
    return entry["payload"] if entry else None


@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for scope, _cid in contexts:
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Samast Technologies",
        "team_members": [],
        "model": "rule-based-deterministic-composer",
        "approach": "Deterministic template composer dispatched by trigger.kind — no LLM call, "
                    "so every field traces back to a pushed context and output is exactly reproducible.",
        "contact_email": "finance@magicpin.in",
        "version": "1.0.0",
        "submitted_at": SUBMITTED_AT,
    }


class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


@app.post("/v1/context")
async def push_context(body: CtxBody):
    if body.scope not in VALID_SCOPES:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": "invalid_scope",
                     "details": f"scope must be one of {sorted(VALID_SCOPES)}, got {body.scope!r}"},
        )
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": cur["version"]},
        )
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}", "stored_at": now_iso()}


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    for trg_id in body.available_triggers:
        if len(actions) >= 20:
            break

        trg = get_payload("trigger", trg_id)
        if not trg:
            continue

        suppression_key = trg.get("suppression_key") or trg_id
        if suppression_key in sent_suppression_keys:
            continue

        merchant_id = trg.get("merchant_id")
        merchant = get_payload("merchant", merchant_id)
        if not merchant:
            continue

        category = get_payload("category", merchant.get("category_slug"))
        if not category:
            continue

        customer_id = trg.get("customer_id")
        customer = get_payload("customer", customer_id) if customer_id else None

        composed = composer.compose(category, merchant, trg, customer)
        conv_id = f"conv_{merchant_id}_{trg_id}_{next(_conv_seq)}"

        conversations[conv_id] = {
            "state": {
                "history": [{"from": "vera", "body": composed["body"]}],
                "sent_bodies": [composed["body"]],
            },
            "merchant_id": merchant_id,
            "customer_id": customer_id,
        }
        sent_suppression_keys.add(suppression_key)

        actions.append({
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed["send_as"],
            "trigger_id": trg_id,
            "template_name": composed["template_name"],
            "template_params": composed.get("template_params", []),
            "body": composed["body"],
            "cta": composed["cta"],
            "suppression_key": suppression_key,
            "rationale": composed["rationale"],
        })

    return {"actions": actions}


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv = conversations.setdefault(
        body.conversation_id,
        {"state": {}, "merchant_id": body.merchant_id, "customer_id": body.customer_id},
    )
    return ch.respond(conv["state"], body.message)


@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    sent_suppression_keys.clear()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
