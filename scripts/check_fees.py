#!/usr/bin/env python3
"""
check_fees.py — hash-gated, three-model consensus G-1055 fee verifier.

How it works:
  1. Download the live USCIS G-1055 PDF.
  2. SHA-256 hash the bytes; compare to stored hash in .fee_check_state.json.
  3. If hash matches: update last_checked timestamp, exit silently.
  4. If hash differs: run three Claude models in parallel (Haiku, Sonnet, Opus),
     take a majority vote on each fee field, diff against fees.json, and route:
       - all 3 agree, no diff:        update verified, exit
       - all 3 agree, edition-only:   auto-apply edition, exit
       - all 3 agree, fee diff:       open high-confidence issue
       - models disagree on any field: open low-confidence side-by-side issue
     Either way, the new hash is stored so we don't re-alert on the same PDF.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
FEES_PATH = REPO_ROOT / "fees.json"
STATE_PATH = REPO_ROOT / ".fee_check_state.json"

G1055_URL = "https://www.uscis.gov/sites/default/files/document/forms/g-1055.pdf"
GH_API = "https://api.github.com"
HTTP_TIMEOUT = 30
CONSENSUS_MODELS = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
]

EXTRACTION_PROMPT = """
You are reviewing USCIS Form G-1055, the official USCIS fee schedule.

Your job: extract paper-filing fees for ONLY the H-2A and H-2B classifications
of Form I-129, plus the I-907 Premium Processing fee for those classifications.

Read ONLY rows explicitly labeled "H-2A" or "H-2B". Do NOT pull values from
H-1B, H-3, L-1, O-1, E, TN, R-1 (alone), or any other classification.

Schema terminology:
- "large"   = Regular employer (more than 25 FTEs)
- "small"   = Small Employer OR Nonprofit (25 or fewer FTEs)
- "named"   = Named beneficiaries (up to 25 per petition)
- "unnamed" = Unnamed beneficiaries

Asylum Program Fee — three tiers on the form:
  $0 (Nonprofit), $300 (Small Employer), $600 (Regular employer).
  For our schema: "small" -> $300, "large" -> $600. Never $0 for "small".

Fraud Prevention and Detection Fee: H-2B only, flat $150 historically.

I-907 Premium Processing — VERY CAREFULLY: we want ONLY the I-129 H-2B/R-1
row (~$1,780). DO NOT report the I-129 H-1B/L/O fee (~$2,965), I-140 fee,
I-539 fee, or I-765 fee. If you read $2,965 you are on the wrong row.

Edition: report MM/DD/YY from the bottom of the form.

Return ONLY this JSON structure (integers, no $ or commas, null if unsure):
{
  "edition": "MM/DD/YY",
  "pp_fee": <I-907 fee for I-129 H-2B/R-1 ONLY>,
  "fees": {
    "h2a": {
      "named":   { "large": {"filing":N,"asylum":N}, "small": {"filing":N,"asylum":N} },
      "unnamed": { "large": {"filing":N,"asylum":N}, "small": {"filing":N,"asylum":N} }
    },
    "h2b": {
      "named":   { "large": {"filing":N,"fraud":N,"asylum":N}, "small": {"filing":N,"fraud":N,"asylum":N} },
      "unnamed": { "large": {"filing":N,"fraud":N,"asylum":N}, "small": {"filing":N,"fraud":N,"asylum":N} }
    }
  }
}

Return ONLY the JSON object, no explanation or markdown.
""".strip()


def now_utc_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"pdf_sha256": "", "last_checked": "", "last_status": ""}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def download_pdf(url):
    r = requests.get(
        url, timeout=HTTP_TIMEOUT,
        headers={"User-Agent": "VisaFeeCalculator-FeeChecker/3.0"},
        allow_redirects=True,
    )
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError("downloaded bytes are not a PDF")
    return r.content


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _parse_json_loosely(text):
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise json.JSONDecodeError("no JSON object found in response", text, 0)
    return json.loads(match.group(0))


def extract_with_model(pdf_b64, model):
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model, max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64",
                  "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": EXTRACTION_PROMPT},
            ],
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return _parse_json_loosely(text)


def run_consensus(pdf_bytes):
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    results, failed = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CONSENSUS_MODELS)) as ex:
        futures = {ex.submit(extract_with_model, pdf_b64, m): m for m in CONSENSUS_MODELS}
        for fut in concurrent.futures.as_completed(futures):
            model = futures[fut]
            try:
                results[model] = fut.result()
                print(f"  OK  {model}")
            except Exception as e:
                failed.append(model)
                print(f"  ERR {model}: {type(e).__name__}: {e}")
    return results, failed


FEE_PATHS = [
    ("edition",), ("pp_fee",),
    ("fees", "h2a", "named",   "large", "filing"),
    ("fees", "h2a", "named",   "large", "asylum"),
    ("fees", "h2a", "named",   "small", "filing"),
    ("fees", "h2a", "named",   "small", "asylum"),
    ("fees", "h2a", "unnamed", "large", "filing"),
    ("fees", "h2a", "unnamed", "large", "asylum"),
    ("fees", "h2a", "unnamed", "small", "filing"),
    ("fees", "h2a", "unnamed", "small", "asylum"),
    ("fees", "h2b", "named",   "large", "filing"),
    ("fees", "h2b", "named",   "large", "fraud"),
    ("fees", "h2b", "named",   "large", "asylum"),
    ("fees", "h2b", "named",   "small", "filing"),
    ("fees", "h2b", "named",   "small", "fraud"),
    ("fees", "h2b", "named",   "small", "asylum"),
    ("fees", "h2b", "unnamed", "large", "filing"),
    ("fees", "h2b", "unnamed", "large", "fraud"),
    ("fees", "h2b", "unnamed", "large", "asylum"),
    ("fees", "h2b", "unnamed", "small", "filing"),
    ("fees", "h2b", "unnamed", "small", "fraud"),
    ("fees", "h2b", "unnamed", "small", "asylum"),
]


def _get_path(d, path):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _set_path(d, path, value):
    cur = d
    for k in path[:-1]:
        cur = cur.setdefault(k, {})
    cur[path[-1]] = value


def reconcile(results):
    consensus, disagreements = {}, []
    for path in FEE_PATHS:
        per_model = {m: _get_path(r, path) for m, r in results.items()}
        non_null = [v for v in per_model.values() if v is not None]
        if not non_null:
            _set_path(consensus, path, None)
            continue
        winner, _ = Counter(non_null).most_common(1)[0]
        _set_path(consensus, path, winner)
        if len(set(non_null)) > 1:
            disagreements.append({"path": ".".join(path), "values": per_model, "winner": winner})
    return consensus, disagreements


def diff_against_fees_json(current, consensus):
    diffs = []
    for path in FEE_PATHS:
        cur_val, new_val = _get_path(current, path), _get_path(consensus, path)
        if cur_val == new_val:
            continue
        label = ".".join(path)
        if path == ("edition",):
            diffs.append(f"Edition: `{
