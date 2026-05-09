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
            diffs.append(f"Edition: `{cur_val}` -> `{new_val}`")
        else:
            cur_fmt = f"${cur_val:,}" if isinstance(cur_val, int) else str(cur_val)
            new_fmt = f"${new_val:,}" if isinstance(new_val, int) else str(new_val)
            diffs.append(f"{label}: **{cur_fmt}** -> **{new_fmt}**")
    return diffs


def open_issue(title, body):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(f"GITHUB_TOKEN/REPOSITORY not set — printing instead.\nTITLE: {title}\n{body}")
        return
    existing = requests.get(
        f"{GH_API}/repos/{repo}/issues",
        params={"state": "open", "per_page": 50},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=HTTP_TIMEOUT,
    ).json()
    for issue in existing:
        if isinstance(issue, dict) and issue.get("title") == title:
            print(f"Issue already open: {issue['html_url']} - skipping duplicate.")
            return
    r = requests.post(
        f"{GH_API}/repos/{repo}/issues",
        json={"title": title, "body": body, "labels": ["fee-check"]},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=HTTP_TIMEOUT,
    )
    print(f"Issue created: {r.json().get('html_url')}" if r.status_code == 201
          else f"Failed: {r.status_code}\n{r.text}")


def update_state(state, *, pdf_hash, status):
    state["pdf_sha256"] = pdf_hash
    state["last_checked"] = today_utc()
    state["last_status"] = status
    save_state(state)


def update_fees_status(current, status, message="", bump_verified=False):
    today = today_utc()
    current["last_checked"] = today
    current["status"] = status
    current["status_message"] = message
    if bump_verified:
        current["verified"] = today
    FEES_PATH.write_text(json.dumps(current, indent=2) + "\n")


def build_high_confidence_body(consensus, current, diffs, succeeded, pdf_hash):
    diff_table = "\n".join(f"| {d.split(':', 1)[0]} | {d.split(':', 1)[1].strip()} |" for d in diffs)
    model_list = ", ".join(f"`{m}`" for m in succeeded)
    return (
        f"## All {len(succeeded)} models agree - fee change is high-confidence\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| **Current `fees.json` edition** | `{current.get('edition')}` |\n"
        f"| **Live G-1055 edition** | `{consensus.get('edition')}` |\n"
        f"| **PDF SHA-256** | `{pdf_hash[:16]}...` |\n"
        f"| **Detected** | {now_utc_str()} |\n"
        f"| **Consensus models** | {model_list} |\n\n---\n\n"
        f"## Detected differences\n\n| Fee | Change |\n|---|---|\n{diff_table}\n\n---\n\n"
        f"## To resolve\n\n"
        f"1. Open the live G-1055: {G1055_URL}\n"
        f"2. Verify each row above against the PDF.\n"
        f"3. Edit `fees.json` to match the new values.\n"
        f"4. Commit & push. Next run will mark `status: ok`.\n"
        f"5. Close this issue.\n"
    )


def build_disagreement_body(results, consensus, current, disagreements, diffs, failed, pdf_hash):
    short_name = lambda m: m.replace("claude-", "").split("-")[0].title()
    model_cols = sorted(results.keys())
    header = "| Field | " + " | ".join(short_name(m) for m in model_cols) + " | fees.json |"
    sep = "|---|" + "---|" * (len(model_cols) + 1)
    rows = []
    for d in disagreements:
        path = d["path"]
        cur_val = _get_path(current, tuple(path.split(".")))
        cells = [f"${v:,}" if isinstance(v, int) else (str(v) if v is not None else "-")
                 for m in model_cols for v in [d["values"].get(m)]]
        cur_cell = f"${cur_val:,}" if isinstance(cur_val, int) else str(cur_val)
        rows.append(f"| `{path}` | " + " | ".join(cells) + f" | {cur_cell} |")
    table = "\n".join([header, sep] + rows)
    failure_note = f"\n\n**Failed models:** {', '.join(failed)}\n" if failed else ""
    return (
        f"## Models disagreed - low-confidence extraction\n\n"
        f"The G-1055 PDF changed (new SHA-256 `{pdf_hash[:16]}...`), but the "
        f"extraction models did not all agree. **Do not trust any single extracted "
        f"number** - verify against the live PDF before updating `fees.json`.\n"
        f"{failure_note}\n"
        f"### Side-by-side per model\n\n{table}\n\n---\n\n"
        f"## To resolve\n\n"
        f"1. Open the live G-1055: {G1055_URL}\n"
        f"2. Read each row above directly from the PDF.\n"
        f"3. Edit `fees.json` with the values you read.\n"
        f"4. Commit & push.\n"
        f"5. Close this issue.\n\n---\n_Detected: {now_utc_str()}_"
    )


def main():
    state = load_state()
    current = json.loads(FEES_PATH.read_text())

    print(f"fees.json edition : {current.get('edition')}")
    print(f"fees.json verified: {current.get('verified')}")
    print(f"stored PDF hash   : {state.get('pdf_sha256') or '(none)'}")

    print(f"\nDownloading G-1055 from {G1055_URL} ...")
    try:
        pdf_bytes = download_pdf(G1055_URL)
    except Exception as e:
        msg = f"G-1055 download failed: {e}"
        print(f"ERROR: {msg}")
        update_fees_status(current, "check_failed", msg)
        update_state(state, pdf_hash=state.get("pdf_sha256", ""), status="check_failed")
        open_issue("[Warning] Fee Check: G-1055 download failed",
                   f"## G-1055 could not be downloaded\n\n**Error:** `{e}`\n\n"
                   f"**Action:** Manually verify fees at {G1055_URL}\n\n"
                   f"---\n_Checked: {now_utc_str()}_")
        return 0

    pdf_hash = sha256_hex(pdf_bytes)
    print(f"  downloaded {len(pdf_bytes):,} bytes; sha256={pdf_hash}")

    if pdf_hash == state.get("pdf_sha256"):
        print("\nOK  PDF unchanged. Updating last_checked and exiting.")
        update_state(state, pdf_hash=pdf_hash, status=state.get("last_status") or "ok")
        current["last_checked"] = today_utc()
        FEES_PATH.write_text(json.dumps(current, indent=2) + "\n")
        return 0

    print(f"\n!   PDF changed. Running consensus extraction across {len(CONSENSUS_MODELS)} models.")
    results, failed = run_consensus(pdf_bytes)

    if not results:
        msg = f"All consensus models failed: {', '.join(failed)}"
        print(f"ERROR: {msg}")
        update_fees_status(current, "check_failed", msg)
        update_state(state, pdf_hash=pdf_hash, status="check_failed")
        open_issue("[Warning] Fee Check: All extraction models failed",
                   f"## All extraction models errored on the new G-1055\n\n"
                   f"**Failed:** {', '.join(failed)}\n\n"
                   f"**Action:** Investigate API status, then re-run. Manually verify "
                   f"fees at {G1055_URL} in the meantime.\n\n---\n_Checked: {now_utc_str()}_")
        return 0

    consensus, disagreements = reconcile(results)
    diffs = diff_against_fees_json(current, consensus)
    succeeded = sorted(results.keys())

    print(f"\nConsensus across {len(succeeded)} model(s).")
    if failed:
        print(f"  failed: {', '.join(failed)}")
    print(f"  disagreements:    {len(disagreements)}")
    print(f"  diffs vs fees.json: {len(diffs)}")

    # Case A: all agree, no diff
    if not disagreements and not diffs:
        update_fees_status(current, "ok", "", bump_verified=True)
        update_state(state, pdf_hash=pdf_hash, status="ok")
        print("\nOK  All models agree, no fee changes. verified bumped.")
        return 0

    # Case B: edition-only
    edition_only = (not disagreements and len(diffs) == 1
                    and diffs[0].startswith("Edition:") and consensus.get("edition"))
    if edition_only:
        old_ed, new_ed = current.get("edition"), consensus["edition"]
        current["edition"] = new_ed
        update_fees_status(current, "ok",
                           f"Edition auto-updated {old_ed} -> {new_ed}; fees unchanged.",
                           bump_verified=True)
        update_state(state, pdf_hash=pdf_hash, status="ok")
        print(f"\nOK  Edition-only: {old_ed} -> {new_ed}. Auto-applied.")
        return 0

    # Case C: disagreement
    if disagreements:
        update_fees_status(current, "changes_detected",
                           f"{len(disagreements)} field(s) had model disagreement.")
        update_state(state, pdf_hash=pdf_hash, status="changes_detected")
        body = build_disagreement_body(results, consensus, current, disagreements, diffs, failed, pdf_hash)
        open_issue(f"[Warning] Fee Check: Model disagreement on G-1055 (live edition {consensus.get('edition')})", body)
        print("\n!   Models disagreed. Low-confidence issue opened.")
        return 0

    # Case D: high-confidence diff
    update_fees_status(current, "changes_detected", f"{len(diffs)} fee change(s) detected by all models.")
    update_state(state, pdf_hash=pdf_hash, status="changes_detected")
    body = build_high_confidence_body(consensus, current, diffs, succeeded, pdf_hash)
    open_issue(f"[Alert] Fee Check: G-1055 fees changed (live edition {consensus.get('edition')})", body)
    print(f"\n!   {len(diffs)} fee change(s). High-confidence issue opened.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
