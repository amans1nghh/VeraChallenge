"""Dev utility (not part of the required deliverable set): renders
submission.jsonl by running composer.compose() over the 30 canonical
(merchant, trigger[, customer]) test pairs from ../expanded/test_pairs.json.

Run: python build_submission.py
"""
import json
from pathlib import Path

import composer

ROOT = Path(__file__).resolve().parent.parent
EXPANDED = ROOT / "expanded"


def load(scope_dir, name_key):
    out = {}
    for f in (EXPANDED / scope_dir).glob("*.json"):
        d = json.load(open(f))
        out[d[name_key]] = d
    return out


def main():
    categories = load("categories", "slug")
    merchants = load("merchants", "merchant_id")
    customers = load("customers", "customer_id")
    triggers = load("triggers", "id")
    pairs = json.load(open(EXPANDED / "test_pairs.json"))["pairs"]

    lines = []
    for pair in pairs:
        trigger = triggers[pair["trigger_id"]]
        merchant = merchants[pair["merchant_id"]]
        category = categories[merchant["category_slug"]]
        customer = customers.get(pair["customer_id"]) if pair.get("customer_id") else None

        composed = composer.compose(category, merchant, trigger, customer)
        lines.append({
            "test_id": pair["test_id"],
            "body": composed["body"],
            "cta": composed["cta"],
            "send_as": composed["send_as"],
            "suppression_key": composed["suppression_key"],
            "rationale": composed["rationale"],
        })

    out_path = Path(__file__).parent / "submission.jsonl"
    with open(out_path, "w") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(f"Wrote {len(lines)} lines to {out_path}")


if __name__ == "__main__":
    main()
