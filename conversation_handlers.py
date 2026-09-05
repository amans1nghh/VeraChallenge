"""Multi-turn reply handling: respond(state, merchant_message) -> dict.

`state` is a plain dict the caller persists per conversation_id (bot.py keeps
one in memory per conversation). Handles the three open challenges called out
in the brief: auto-reply detection + escalation, intent-transition routing,
and graceful exit on hostility/repeated silence.
"""
from __future__ import annotations

AUTO_REPLY_PATTERNS = [
    "thank you for contacting", "will respond shortly", "will get back to you",
    "team tak pahuncha", "hamari team tak", "we will revert", "auto reply",
    "currently unavailable", "busy right now",
]
SELF_DECLARE_BOT = [
    "automated assistant", "i am a bot", "main ek automated", "this is an automated",
]
HOSTILE_PATTERNS = [
    "stop messaging", "stop sending", "spam", "useless", "bothering me",
    "harassment", "leave me alone", "not interested. stop", "stfu", "fuck off",
]
INTENT_PATTERNS = [
    "let's do it", "lets do it", "go ahead", "ok let's do it", "ok lets do it",
    "yes let's", "yes lets", "proceed", "confirm", "sure, go for it", "sure go for it",
    "haan kar do", "kar do", "chalo karte hain",
]
OFF_TOPIC_HINTS = [
    "gst", "income tax", "loan", "insurance claim", "visa", "passport",
]


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


# Short generic replies ("yes", "ok", "thanks") legitimately recur in a real
# engaged conversation -- only long, specific repeats are auto-reply signal.
_REPEAT_MIN_LEN = 20


def _is_repeat(state: dict, lower_msg: str) -> bool:
    if len(lower_msg) < _REPEAT_MIN_LEN:
        return False
    prior = [h["body"] for h in state["history"] if h["from"] == "merchant"]
    prior = prior[:-1]  # exclude the message we just appended
    return prior.count(lower_msg) >= 1


def _send(state: dict, body: str, cta: str, rationale: str) -> dict:
    n = state["sent_bodies"].count(body)
    if n:
        body = f"{body} (following up again, take {n + 1})"
    state["sent_bodies"].append(body)
    state["history"].append({"from": "vera", "body": body})
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def respond(state: dict, merchant_message: str) -> dict:
    state.setdefault("history", [])
    state.setdefault("sent_bodies", [])
    state.setdefault("auto_reply_count", 0)
    state.setdefault("mode", "qualifying")
    state.setdefault("ended", False)

    lower = _norm(merchant_message)
    state["history"].append({"from": "merchant", "body": lower})

    if state["ended"]:
        return {"action": "end", "rationale": "Conversation already closed; not re-engaging."}

    if any(p in lower for p in SELF_DECLARE_BOT):
        state["ended"] = True
        return {"action": "end",
                "rationale": "Merchant's own message self-identifies as an automated assistant; "
                              "owner unreachable on this thread, closing gracefully."}

    if any(p in lower for p in AUTO_REPLY_PATTERNS) or _is_repeat(state, lower):
        state["auto_reply_count"] += 1
        n = state["auto_reply_count"]
        if n == 1:
            return _send(state,
                         "Looks like an auto-reply 😊 When the owner sees this, a quick reply helps me move faster.",
                         "open_ended",
                         "First canned auto-reply detected; one light nudge for the owner, not repeating the full pitch.")
        if n == 2:
            return {"action": "wait", "wait_seconds": 14400,
                    "rationale": "Second identical auto-reply in a row — owner likely not at phone. Backing off 4h before retrying."}
        state["ended"] = True
        return {"action": "end",
                "rationale": f"Auto-reply {n}x in a row with zero real engagement signal; closing to stop burning turns."}

    if any(p in lower for p in HOSTILE_PATTERNS):
        state["ended"] = True
        return {"action": "end",
                "rationale": "Merchant explicitly signaled not-interested/hostile; closing without further pitch."}

    if any(p in lower for p in INTENT_PATTERNS):
        state["mode"] = "action"
        return _send(state,
                     "Great — starting on it now. I'll have something concrete for you in a moment; reply CONFIRM once you see it to send it out.",
                     "binary_confirm_cancel",
                     "Merchant gave explicit go-ahead; switching from qualifying to action mode immediately, no further qualifying question.")

    if any(p in lower for p in OFF_TOPIC_HINTS):
        return _send(state,
                     "That's outside what I can help with directly — best to check with your CA/agent on that one. "
                     "Coming back to where we were: want me to go ahead with the last thing I offered?",
                     "open_ended",
                     "Off-topic ask politely declined; redirected back to the open thread without losing context.")

    return _send(state,
                 "Got it — noted. I'll get that moving and follow up with what's ready.",
                 "open_ended",
                 "Engaged reply; acknowledging and advancing without re-pitching from scratch.")
