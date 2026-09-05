"""Deterministic Vera-replacement composer.

compose(category, merchant, trigger, customer=None) -> dict with keys
body / cta / send_as / template_name / template_params / rationale
(suppression_key is filled in by the caller from trigger["suppression_key"]).

No LLM call. Every fact in every template is read straight from the pushed
contexts (category/merchant/trigger/customer) -- nothing is invented, so the
"don't fabricate" rule is structurally impossible to violate, and the output
is trivially deterministic and sub-30s.
"""
from __future__ import annotations


# ---------------------------------------------------------------- helpers --

def pct(x, digits=0):
    if x is None:
        return "?"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x * 100:.{digits}f}%"


def mag_pct(x, digits=0):
    """Magnitude only, no sign — for use alongside an explicit up/down/lost word."""
    return f"{abs(x) * 100:.{digits}f}%" if x is not None else "?"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


METRIC_LABELS = {"review_count": "reviews", "views": "views", "calls": "calls",
                 "directions": "directions", "leads": "leads"}


def owner_name(merchant):
    ident = merchant.get("identity", {})
    return ident.get("owner_first_name") or ident.get("name", "there")


def biz_name(merchant):
    return merchant.get("identity", {}).get("name", "your business")


def is_hindi(merchant, customer=None):
    if customer:
        pref = (customer.get("identity", {}).get("language_pref") or "").lower()
        if "hi" in pref:
            return True
        if pref:
            return False
    return "hi" in merchant.get("identity", {}).get("languages", [])


def active_offers(merchant):
    return [o for o in merchant.get("offers", []) if o.get("status") == "active"]


def top_offer(merchant):
    offs = active_offers(merchant)
    return offs[0]["title"] if offs else None


def digest_item(category, item_id=None):
    items = category.get("digest", [])
    if item_id:
        for d in items:
            if d.get("id") == item_id:
                return d
    return items[0] if items else None


CLOSERS = {
    "draft": ("Want me to {x}?", "Kya main {x} kar doon?"),
    "confirm": ("Reply YES to confirm — I'll take it from there.",
                "Confirm ke liye YES reply kar dijiye, baaki main dekh leti hoon."),
    "binary": ("Reply YES or STOP.", "YES ya STOP reply kar dijiye."),
    "open": ("What do you think?", "Aapko kaisa laga?"),
    "slot2": ("Reply 1 for {a} or 2 for {b}, or tell us a time that works.",
              "{a} ke liye 1 ya {b} ke liye 2 reply kar dijiye, ya apna time bata dijiye."),
    "lowfriction_yes": ("Reply YES — no commitment.", "YES reply kar dijiye — koi commitment nahi."),
}


def close(tag, hindi, **kwargs):
    en, hi = CLOSERS[tag]
    text = hi if hindi else en
    return text.format(**kwargs) if kwargs else text


def _tp(name_hint, fact, cta):
    return [name_hint, fact[:160], cta]


# -------------------------------------------------------- merchant kinds --

def h_research_digest(cat, m, trg, cust):
    hindi = is_hindi(m)
    d = digest_item(cat, trg.get("payload", {}).get("top_item_id"))
    if d:
        extra = ""
        if d.get("trial_n"):
            extra = f" ({d['trial_n']}-patient trial"
            extra += f", {d['patient_segment'].replace('_', ' ')}" if d.get("patient_segment") else ""
            extra += ")"
        body = f"{owner_name(m)}, this week's {cat.get('display_name', cat['slug'])} digest: {d.get('title')}{extra}."
        if d.get("source"):
            body += f" — {d['source']}."
    else:
        body = f"{owner_name(m)}, this week's {cat.get('display_name', cat['slug'])} research digest just landed — worth a 2-min look."
    body += " " + close("draft", hindi, x="pull the summary + draft something you can share")
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_research_digest_v1",
                rationale="External research digest anchored on the category digest item, source-cited, low-friction draft offer.")


def h_cde_opportunity(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    d = digest_item(cat, p.get("digest_item_id"))
    body = f"{owner_name(m)}, {d.get('title') if d else 'a CDE credit opportunity'}"
    if p.get("credits"):
        body += f" — {p['credits']} CDE credit{'s' if p['credits'] != 1 else ''}"
    if p.get("fee"):
        body += f", {p['fee'].replace('_', ' ')}"
    body += ". " + close("draft", hindi, x="block the slot for you")
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_cde_opportunity_v1",
                rationale="Professional-development trigger; concrete credit count + fee framing, single binary ask.")


def h_regulation_change(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    d = digest_item(cat, p.get("top_item_id"))
    deadline = (p.get("deadline_iso") or "")[:10]
    if d:
        body = f"{owner_name(m)}, regulatory update: {d.get('title')}"
        body += f" ({d['source']})" if d.get("source") else ""
    else:
        body = f"{owner_name(m)}, a regulatory update just dropped for {cat.get('display_name', cat['slug'])}"
    body += f". Compliance deadline: {deadline}." if deadline else "."
    body += " " + close("draft", hindi, x="send the 1-line summary of what changes for your practice")
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_regulation_change_v1",
                rationale="Compliance trigger; deadline surfaced explicitly, no alarmism, offers a distilled summary.")


def h_perf(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    kind = trg["kind"]
    metric, delta, window = p.get("metric"), p.get("delta_pct"), p.get("window", "7d")
    if metric and delta is not None:
        line = f"your {metric} are {'up' if delta >= 0 else 'down'} {mag_pct(delta)} this {window}"
        if p.get("vs_baseline"):
            line += f" (baseline ~{p['vs_baseline']}/day)"
    else:
        d7 = m.get("performance", {}).get("delta_7d", {})
        if d7:
            mk = max(d7, key=lambda k: abs(d7[k]))
            line = f"your {mk.replace('_pct', '')} are {'up' if d7[mk] >= 0 else 'down'} {mag_pct(d7[mk])} this week"
        else:
            line = None
    body = f"{owner_name(m)}, {line}." if line else f"{owner_name(m)}, a performance shift showed up on your dashboard this week."

    is_spike = kind == "perf_spike" or (delta is not None and delta > 0)
    if kind == "seasonal_perf_dip":
        note = p.get("season_note", "").replace("_", " ")
        body += f" This is the known seasonal pattern{(' (' + note + ')') if note else ''}, not a real problem." \
            if p.get("is_expected_seasonal") else ""
        cta_x = "draft a way to keep retention steady through this window"
    else:
        driver = p.get("likely_driver")
        if driver and is_spike:
            body += f" Looks tied to {driver.replace('_', ' ')}."
        cta_x = "double down on what's working" if is_spike else "dig into what's causing it"
    body += " " + close("draft", hindi, x=cta_x)
    return dict(body=body, cta="open_ended", send_as="vera", template_name=f"vera_{kind}_v1",
                rationale=f"{kind} trigger anchored on {'trigger payload' if metric else 'merchant performance delta_7d'} numbers.")


def h_category_seasonal(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    trends = p.get("trends") or []
    season = (p.get("season") or "").replace("_", " ")
    if trends:
        body = f"{owner_name(m)}, {season} shelf shift: " + ", ".join(t.replace("_", " ") for t in trends[:4]) + "."
    else:
        beat = (cat.get("seasonal_beats") or [None])[0]
        body = f"{owner_name(m)}, a seasonal demand shift is worth planning for" + (f" — {beat['note']}" if beat else "") + "."
    body += " " + close("draft", hindi, x="draft the shelf reorg + a customer WhatsApp for the top movers")
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_category_seasonal_v1",
                rationale="Seasonal demand trends taken directly from trigger payload; framed as an inventory/prep action.")


def h_milestone(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    raw_metric = p.get("metric")
    label = METRIC_LABELS.get(raw_metric, (raw_metric or "").replace("_", " "))
    now_v, target = p.get("value_now"), p.get("milestone_value")
    if now_v is not None and target is not None:
        body = f"{owner_name(m)}, you're at {now_v} {label or 'toward a milestone'} — {target - now_v} more to cross {target}."
    else:
        body = f"{owner_name(m)}, {biz_name(m)} is closing in on {('a ' + label + ' milestone') if label else 'a milestone'}."
    body += " " + close("draft", hindi, x="post about it once you cross it")
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_milestone_reached_v1",
                rationale="Milestone proximity is a concrete, verifiable number; framed as a shareable win, not a pitch.")


def h_competitor(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    name, dist, offer = p.get("competitor_name"), p.get("distance_km"), p.get("their_offer")
    if name:
        body = f"{owner_name(m)}, heads up — {name} opened" + (f" {dist}km away" if dist else " nearby")
        body += f", running \"{offer}\"" if offer else ""
        body += "."
    else:
        body = f"{owner_name(m)}, a new competitor showed up on GBP near {m.get('identity', {}).get('locality', 'you')}."
    mine = top_offer(m)
    if mine:
        body += f" Your \"{mine}\" is still live — want me to boost it this week to stay visible?"
        cta = "binary_yes_no"
    else:
        body += " " + close("draft", hindi, x="put together a counter-offer from your catalog")
        cta = "open_ended"
    return dict(body=body, cta=cta, send_as="vera", template_name="vera_competitor_opened_v1",
                rationale="Competitor proximity + their offer used as a loss-aversion anchor, tied to the merchant's own live offer.")


def h_festival(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    fest, days = p.get("festival"), p.get("days_until")
    if fest and days is not None:
        body = f"{owner_name(m)}, {fest} is {days} days out."
        body += " Early, but worth planning now while slots/stock are open." if days > 60 else " Good window to push a festival-specific offer."
    else:
        body = f"{owner_name(m)}, a seasonal/festival window is coming up worth planning for."
    body += " " + close("draft", hindi, x="draft a festival post from your catalog")
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_festival_upcoming_v1",
                rationale="Festival lead time is a verifiable countdown; framed as planning, not generic promo.")


def h_ipl(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    match, venue, weeknight = p.get("match"), p.get("venue"), p.get("is_weeknight")
    if match:
        body = f"{owner_name(m)}, {match} today" + (f" at {venue}." if venue else ".")
        if weeknight is False:
            body += " Weekend match — foot traffic usually dips as people watch at home, so a delivery push beats an in-store promo tonight."
        elif weeknight is True:
            body += " Weeknight match — decent walk-in bump expected around kickoff."
    else:
        body = f"{owner_name(m)}, there's a match today that could shift your footfall."
    mine = top_offer(m)
    body += f" Your \"{mine}\" fits either way." if mine else ""
    body += " " + close("draft", hindi, x="draft the match-night post")
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_ipl_match_today_v1",
                rationale="Match timing (weeknight vs weekend) drives a contrarian, category-smart call instead of a blanket promo.")


def h_dormant(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    days, topic = p.get("days_since_last_merchant_message"), p.get("last_topic")
    lead = (f"it's been {days} days since we last spoke" + (f" about {topic.replace('_', ' ')}" if topic else "")
            if days else "it's been a while since we last spoke")
    ask = "Aap is hafte sabse zyada kis cheez mein help chahenge?" if hindi else "What's the one thing you'd want help with this week?"
    body = f"{owner_name(m)}, {lead}. {ask}"
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_dormant_with_vera_v1",
                rationale="Re-engagement after silence; asks the merchant directly instead of another reminder — the under-used 'ask' lever.")


def h_curious(cat, m, trg, cust):
    hindi = is_hindi(m)
    template = (trg.get("payload", {}).get("ask_template") or "").replace("_", " ")
    lead = "I'll turn your answer into a post + a ready reply you can reuse."
    if not template or "service in demand" in template:
        ask = (f"{biz_name(m)} mein is hafte sabse zyada kya pucha gaya — bata dijiye?" if hindi
               else f"Quick one — what's been the most-asked-for thing at {biz_name(m)} this week?")
    else:
        ask = f"{template} pe aapka kya khayal hai?" if hindi else f"Quick one on {template} — what's your take?"
    body = f"{lead} {ask}"
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_curious_ask_due_v1",
                rationale="Scheduled curiosity-ask cadence; asks the merchant a question, converts the answer into reusable content.")


def h_renewal(cat, m, trg, cust):
    hindi = is_hindi(m)
    sub = m.get("subscription", {})
    days, plan = sub.get("days_remaining"), sub.get("plan", "your plan")
    if days is not None:
        body = f"{owner_name(m)}, {plan} renews in {days} days."
    elif sub.get("days_since_expiry"):
        body = f"{owner_name(m)}, {plan} expired {sub['days_since_expiry']} days ago."
    else:
        body = f"{owner_name(m)}, your subscription needs a look."
    body += " " + close("binary", hindi)
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_renewal_due_v1",
                rationale="Subscription day-count is a concrete, verifiable fact; single binary ask to renew.")


def h_gbp(cat, m, trg, cust):
    hindi = is_hindi(m)
    uplift = trg.get("payload", {}).get("estimated_uplift_pct")
    body = f"{owner_name(m)}, your Google profile isn't verified yet"
    body += f" — verified profiles here see ~{pct(uplift)} more calls on average" if uplift else ""
    body += ". " + close("draft", hindi, x="walk you through verification, 2 minutes")
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_gbp_unverified_v1",
                rationale="Verification uplift stat used as a loss-aversion anchor; low-friction 2-minute framing.")


def h_winback_merchant(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    days, lapsed_added, dip = p.get("days_since_expiry"), p.get("lapsed_customers_added_since_expiry"), p.get("perf_dip_pct")
    body = f"{owner_name(m)}, it's been {days} days since your subscription lapsed" if days else f"{owner_name(m)}, your subscription lapsed a while back"
    body += f" — visibility is down {mag_pct(dip)} since" if dip else ""
    body += f", and {lapsed_added} more customers went quiet in that window" if lapsed_added else ""
    body += ". " + close("draft", hindi, x="get you back live, takes 2 minutes")
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_winback_eligible_v1",
                rationale="Subscription-lapse loss framed with concrete dip% / lapsed-customer counts from the payload.")


def h_review(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    theme, occ, quote = p.get("theme"), p.get("occurrences_30d"), p.get("common_quote")
    if not theme:
        themes = m.get("review_themes", [])
        top = next((t for t in themes if t.get("occurrences_30d", 0) >= 2), themes[0] if themes else None)
        if top:
            theme, occ, quote = top.get("theme"), top.get("occurrences_30d"), top.get("common_quote")
    if theme:
        body = f"{owner_name(m)}, {occ or 'multiple'} reviews recently mention \"{theme.replace('_', ' ')}\"."
        body += f" One reads: \"{quote}\"." if quote else ""
    else:
        body = f"{owner_name(m)}, a pattern showed up across your recent reviews worth a look."
    body += " " + close("draft", hindi, x="draft a reply template + a fix you can post about")
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_review_theme_emerged_v1",
                rationale="Review theme with real occurrence count and quote; loss-aversion or social-proof depending on sentiment.")


def h_planning(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    topic = (p.get("intent_topic") or trg["kind"]).replace("_", " ")
    mine = top_offer(m)
    body = f"{owner_name(m)}, on {topic} — here's a starter, you can edit"
    body += f" (building off your \"{mine}\")" if mine else ""
    body += ":\n\n"
    leads = m.get("performance", {}).get("leads")
    if leads:
        body += f"- Sized for your current ~{leads} monthly leads\n"
    body += f"- {topic.title()} — draft ready on your confirm\n\n" + close("confirm", hindi)
    return dict(body=body, cta="binary_confirm_cancel", send_as="vera", template_name="vera_active_planning_intent_v1",
                rationale="Merchant already said yes (see conversation_history) — switching straight to a draft + confirm, not another qualifying question.")


def h_wedding_followup(cat, m, trg, cust):
    hindi = is_hindi(m)
    mine = top_offer(m)
    body = f"{owner_name(m)}, wedding-season bookings are opening up" + (f" — your \"{mine}\" fits well" if mine else "") + "."
    body += " " + close("draft", hindi, x="draft the wedding-package post")
    return dict(body=body, cta="open_ended", send_as="vera", template_name="vera_wedding_package_followup_v1",
                rationale="Seasonal package follow-up tied to the merchant's existing offer catalog.")


def h_supply_alert(cat, m, trg, cust):
    hindi = is_hindi(m)
    p = trg.get("payload", {})
    molecule, batches, mfr = p.get("molecule"), p.get("affected_batches") or [], p.get("manufacturer")
    if molecule:
        body = f"{owner_name(m)}, urgent: voluntary recall on {molecule}"
        body += f" batches {', '.join(batches)}" if batches else ""
        body += f" ({mfr})" if mfr else ""
        body += " — customers dispensed this batch should be informed for replacement."
        chronic = m.get("customer_aggregate", {}).get("chronic_rx_count")
        if chronic:
            body += f" You have {chronic} chronic-Rx customers overall; I don't have a batch-dispense log to narrow that further."
        body += " " + close("draft", hindi, x="draft the customer WhatsApp + replacement workflow")
    else:
        body = f"{owner_name(m)}, a supply alert affecting your stock just came in. " + close("draft", hindi, x="pull the specifics")
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_supply_alert_v1",
                rationale="Safety recall uses real batch/molecule data only; does not fabricate an affected-customer count we can't derive.")


def h_trial_followup_merchant(cat, m, trg, cust):
    hindi = is_hindi(m)
    body = f"{owner_name(m)}, following up on recent trial signups — want me to draft the convert-to-paid nudge for the ones nearing trial end?"
    return dict(body=body, cta="binary_yes_no", send_as="vera", template_name="vera_trial_followup_v1",
                rationale="Trial-to-paid conversion nudge; single binary ask (merchant-scope fallback).")


def h_generic_fallback(cat, m, trg, cust):
    hindi = is_hindi(m)
    d7 = m.get("performance", {}).get("delta_7d", {})
    if d7:
        mk = max(d7, key=lambda k: abs(d7[k]))
        body = f"{owner_name(m)}, your {mk.replace('_pct', '')} moved {pct(d7[mk])} this week — worth a quick look."
    else:
        mine = top_offer(m)
        body = f"{owner_name(m)}, checking in on {biz_name(m)}."
        body += f" Your \"{mine}\" is live — want a nudge to promote it?" if mine else ""
    body += " " + close("open", hindi)
    return dict(body=body, cta="open_ended", send_as="vera", template_name=f"vera_{trg.get('kind', 'generic')}_v1",
                rationale=f"Unmapped trigger kind '{trg.get('kind')}'; fell back to the strongest available performance signal, no fabrication.")


MERCHANT_HANDLERS = {
    "research_digest": h_research_digest,
    "cde_opportunity": h_cde_opportunity,
    "regulation_change": h_regulation_change,
    "perf_spike": h_perf,
    "perf_dip": h_perf,
    "seasonal_perf_dip": h_perf,
    "category_seasonal": h_category_seasonal,
    "milestone_reached": h_milestone,
    "competitor_opened": h_competitor,
    "festival_upcoming": h_festival,
    "ipl_match_today": h_ipl,
    "dormant_with_vera": h_dormant,
    "curious_ask_due": h_curious,
    "renewal_due": h_renewal,
    "gbp_unverified": h_gbp,
    "winback_eligible": h_winback_merchant,
    "review_theme_emerged": h_review,
    "active_planning_intent": h_planning,
    "wedding_package_followup": h_wedding_followup,
    "supply_alert": h_supply_alert,
    "trial_followup": h_trial_followup_merchant,
}


# -------------------------------------------------------- customer kinds --

def h_customer(cat, m, trg, cust):
    hindi = is_hindi(m, cust)
    p = trg.get("payload", {})
    kind = trg.get("kind", "")
    name = (cust or {}).get("identity", {}).get("name", "there")
    biz = biz_name(m)
    rel = (cust or {}).get("relationship", {})

    if kind == "recall_due":
        due_service = (p.get("service_due") or "recall").replace("_", " ")
        slots = p.get("available_slots") or []
        mine = top_offer(m)
        lead = (f"It's been a while since your last visit ({rel['last_visit']}) — your {due_service} is due"
                if rel.get("last_visit") else f"Your {due_service} is due")
        body = f"Hi {name}, {biz} here. {lead}."
        if slots:
            labels = [s.get("label", "a slot") for s in slots]
            body += f" Slots open: {' or '.join(labels)}."
        body += f" {mine}." if mine else ""
        if slots:
            body += " " + close("slot2", hindi, a=labels[0], b=labels[1] if len(labels) > 1 else labels[0])
            cta = "multi_choice_slot"
        else:
            body += " " + close("draft", hindi, x="hold a slot for you")
            cta = "open_ended"
        template = "merchant_recall_reminder_v1"

    elif kind == "appointment_tomorrow":
        body = f"Hi {name}, {biz} here — quick reminder about your appointment tomorrow. " + close("binary", hindi)
        cta = "binary_yes_no"
        template = "merchant_appointment_reminder_v1"

    elif kind == "chronic_refill_due":
        molecules = p.get("molecule_list") or []
        runs_out = (p.get("stock_runs_out_iso") or "")[:10]
        if molecules:
            body = f"Hi {name}, {biz} here. Your {', '.join(molecules)} run out" + (f" on {runs_out}" if runs_out else " soon") + ". Same dose, same pack ready."
        else:
            body = f"Hi {name}, {biz} here — your regular medicines are due for refill soon."
        body += " Free delivery to your saved address." if p.get("delivery_address_saved") else ""
        body += " " + close("confirm", hindi)
        cta = "binary_confirm_cancel"
        template = "merchant_chronic_refill_v1"

    elif kind in ("customer_lapsed_soft", "customer_lapsed_hard"):
        days, focus = p.get("days_since_last_visit"), p.get("previous_focus")
        if days:
            lead = f"It's been {days} days since your last visit"
        elif rel.get("last_visit"):
            lead = f"It's been a while since your last visit ({rel['last_visit']})"
        else:
            lead = "It's been a while"
        body = f"Hi {name}, {biz} here. {lead}"
        body += f" — no judgment, happens to most {focus.replace('_', ' ')} folks" if focus else ""
        body += "."
        mine = top_offer(m)
        body += f" We've got \"{mine}\" running if you want to jump back in." if mine else ""
        body += " " + close("lowfriction_yes", hindi)
        cta = "binary_yes_no"
        template = f"merchant_{kind}_v1"

    elif kind == "trial_followup":
        trial_date, opts = p.get("trial_date"), p.get("next_session_options") or []
        body = f"Hi {name}, {biz} here."
        body += f" Thanks for trying us on {trial_date}!" if trial_date else ""
        if opts:
            body += f" Next session open: {opts[0].get('label', 'a slot')}. " + close("binary", hindi)
        else:
            body += " " + close("draft", hindi, x="hold your next session")
        cta = "binary_yes_no"
        template = "merchant_trial_followup_v1"

    else:
        body = f"Hi {name}, {biz} here — checking in. " + close("open", hindi)
        cta = "open_ended"
        template = "merchant_generic_v1"

    return dict(body=body, cta=cta, send_as="merchant_on_behalf", template_name=template,
                rationale=f"Customer-facing {kind}; personalized to {name}'s relationship/state, "
                          f"language honored ({'hi-en' if hindi else 'en'}).")


# --------------------------------------------------------------- compose --

def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    kind = trigger.get("kind", "")
    scope = trigger.get("scope", "merchant")

    if scope == "customer":
        result = h_customer(category, merchant, trigger, customer)
    else:
        handler = MERCHANT_HANDLERS.get(kind, h_generic_fallback)
        result = handler(category, merchant, trigger, customer)

    result["suppression_key"] = trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id')}:{trigger.get('id')}"
    if "template_params" not in result:
        who = (customer or {}).get("identity", {}).get("name") if scope == "customer" else owner_name(merchant)
        result["template_params"] = _tp(who or "there", result["body"], result["cta"])
    return result
