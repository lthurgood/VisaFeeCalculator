#!/usr/bin/env python3
"""
check_fees.py
Downloads the USCIS G-1055 PDF, sends it to Claude for intelligent fee
extraction, compares extracted values against fees.json, and opens a GitHub
Issue automatically if anything has changed or cannot be verified.

Run manually: ANTHROPIC_API_KEY=sk-... python scripts/check_fees.py
Run via CI: see .github/workflows/check-fees.yml
"""

import base64
import io
import json
import re
import os
import sys
from datetime import datetime, timezone

import anthropic
import requests

# ── Config ────────────────────────────────────────────────────────────────────

G1055_URL = "https://www.uscis.gov/sites/default/files/document/forms/g-1055.pdf"
FEES_JSON = os.path.join(os.path.dirname(__file__), "..", "fees.json")
GH_API = "https://api.github.com"
TIMEOUT = 30
MODEL = "claude-haiku-4-5"  # fast, cheap — ideal for structured extraction

# ── Extraction prompt ────────────────────────────────────────────────────────
# This is intentionally verbose. The G-1055 contains fees for many forms and
# classifications, and Claude has previously grabbed the wrong rows (e.g. the
# H-1B premium processing fee instead of the H-2B one). The prompt narrows
# the scope explicitly and includes known-correct values as sanity anchors.

EXTRACTION_PROMPT = """
You are reviewing USCIS Form G-1055, the official USCIS fee schedule.

Your job: extract paper-filing fees for ONLY the H-2A and H-2B classifications
of Form I-129 (Petition for a Nonimmigrant Worker), plus the I-907 Premium
Processing fee that applies to those specific classifications.

The G-1055 contains fees for many forms and classifications. Read ONLY rows
explicitly labeled "H-2A" or "H-2B". Do NOT pull values from H-1B, H-3, L-1,
O-1, E, TN, R-1 (alone), or any other classification.

---

# Schema terminology

In the JSON output below:
- "large"   = Regular employer (more than 25 full-time-equivalent employees)
- "small"   = Small Employer OR Nonprofit (25 or fewer FTEs)
- "named"   = Named beneficiaries (up to 25 named workers per petition)
- "unnamed" = Unnamed beneficiaries (no per-petition limit)

---

# I-129 filing fees (H-2A and H-2B)

For each visa type the form lists 4 filing-fee variants:
  1. NAMED beneficiaries  + Regular employer  (>25 FTE)  → "named.large.filing"
  2. NAMED beneficiaries  + Small/Nonprofit   (≤25 FTE)  → "named.small.filing"
  3. UNNAMED beneficiaries + Regular employer (>25 FTE)  → "unnamed.large.filing"
  4. UNNAMED beneficiaries + Small/Nonprofit  (≤25 FTE)  → "unnamed.small.filing"

These are I-129 filing fees only — do not include the Asylum Program Fee or
the Fraud Prevention and Detection Fee in the "filing" amounts.

---

# Asylum Program Fee — READ CAREFULLY

The G-1055 lists the Asylum Program Fee at THREE different amounts:
  • $0   — Nonprofit
  • $300 — Small Employer (25 or fewer FTEs)
  • $600 — Regular employer (more than 25 FTEs)

For our schema:
  • "asylum" under "large" → use the $600 (Regular employer) tier
  • "asylum" under "small" → use the $300 (Small Employer) tier
                             — NOT $0, and NOT $600

These tiers have been stable for years. If a "small" row reads $600 or $0,
you have almost certainly misread the row — re-check before reporting.

---

# Fraud Prevention and Detection Fee (H-2B only)

This is a flat fee that applies to ALL H-2B petitions regardless of size or
named/unnamed status. It has historically been $150. H-2A petitions do NOT
have this fee, so do not include "fraud" anywhere under "h2a".

---

# I-907 Premium Processing Fee — READ VERY CAREFULLY

The G-1055 lists I-907 Premium Processing fees for many different forms and
classifications. We need ONLY the I-907 fee that applies to Form I-129 with
H-2B (or H-2B/R-1) classification. As of 2026 this fee is $1,780.

DO NOT report any of these other I-907 fees:
  • I-129 with H-1B / H-3 / L-1 / O-1 / E / TN classification → ~$2,965
  • I-140 (immigrant petitions for workers)                   → ~$2,965
  • I-539 (status change/extension for F, J, M)               → ~$2,075
  • I-765 (employment authorization)                          → ~$1,780

Look specifically for the row labeled with both "I-129" AND "H-2B" (often
shown as "H-2B/R-1"). If the number you read is $2,965, you are on the
wrong row — go back and find the H-2B row.

---

# Edition

Report the edition date printed at the bottom of the G-1055 in MM/DD/YY format
(e.g. "05/06/26").

---

# Output format

Return ONLY a JSON object in this exact structure. Use integer dollar amounts
(no $ signs, no commas, no decimals). If a value cannot be confidently found,
set it to null rather than guessing.

{
  "edition": "MM/DD/YY",
  "pp_fee": <I-907 fee for I-129 H-2B/R-1 ONLY>,
  "fees": {
    "h2a": {
      "named": {
        "large": { "filing": <amount>, "asylum": <amount> },
        "small": { "filing": <amount>, "asylum": <amount> }
      },
      "unnamed": {
        "large": { "filing": <amount>, "asylum": <amount> },
        "small": { "filing": <amount>, "asylum": <amount> }
      }
    },
    "h2b": {
      "named": {
        "large": { "filing": <amount>, "fraud": <amount>, "asylum": <amount> },
        "small": { "filing": <amount>, "fraud": <amount>, "asylum": <amount> }
      },
      "unnamed": {
        "large": { "filing": <amount>, "fraud": <amount>, "asylum": <amount> },
        "small": { "filing": <amount>, "fraud": <amount>, "asylum": <amount> }
      }
    }
  }
}

Return ONLY the JSON object, no explanation or markdown.
""".strip()

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def save_status(current: dict, status: str, message: str = "", bump_verified: bool = False) -> None:
    """Write status fields back to fees.json.

    status        : 'ok' | 'changes_detected' | 'check_failed'
    message       : short human-readable note (empty for 'ok')
    bump_verified : if True, also advance "verified" to today (success path only)
    """
    today = today_utc()
    current["last_checked"] = today
    current["status"] = status
    current["status_message"] = message
    if bump_verified:
        current["verified"] = today
    with open(FEES_JSON, "w") as f:
        json.dump(current, f, indent=2)
        f.write("\n")


def download_pdf(url: str) -> bytes:
    r = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "VisaFeeCalculator-FeeChecker/2.0"},
        allow_redirects=True,
    )
    r.raise_for_status()
    return r.content


def extract_fees_with_claude(pdf_bytes: bytes) -> dict:
    """Send the PDF to Claude and get back a structured fee object."""
    client = anthropic.Anthropic()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT,
                    },
                ],
            }
        ],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return _parse_json_loosely(text)


def _parse_json_loosely(text: str) -> dict:
    """Strip markdown fences / preamble before json.loads."""
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise json.JSONDecodeError("no JSON object found in response", text, 0)
    return json.loads(match.group(0))


def diff_fees(current: dict, extracted: dict) -> list[str]:
    """Return a list of human-readable differences between two fee dicts."""
    diffs = []
    if current.get("edition") != extracted.get("edition"):
        diffs.append(
            f"Edition: `{current.get('edition')}` → `{extracted.get('edition')}`"
        )
    if current.get("pp_fee") != extracted.get("pp_fee"):
        diffs.append(
            f"I-907 Premium Processing: **${current.get('pp_fee'):,}** → **${extracted.get('pp_fee'):,}**"
        )
    for visa in ("h2a", "h2b"):
        for workers in ("named", "unnamed"):
            for size in ("large", "small"):
                cur = current["fees"][visa][workers][size]
                ext = extracted.get("fees", {}).get(visa, {}).get(workers, {}).get(size, {})
                for component, cur_val in cur.items():
                    ext_val = ext.get(component)
                    if cur_val != ext_val:
                        label = f"{visa.upper()} / {workers} / {'Regular' if size == 'large' else 'Small-NP'} / {component}"
                        diffs.append(
                            f"{label}: **${cur_val:,}** → **${ext_val:,}**"
                        )
    return diffs


# ── GitHub Issue ──────────────────────────────────────────────────────────────

def open_issue(title: str, body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN / GITHUB_REPOSITORY not set — printing issue instead.\n")
        print(f"TITLE: {title}\n\nBODY:\n{body}")
        return

    existing = requests.get(
        f"{GH_API}/repos/{repo}/issues",
        params={"state": "open", "per_page": 50},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=TIMEOUT,
    ).json()
    for issue in existing:
        if isinstance(issue, dict) and issue.get("title") == title:
            print(f"Issue already open: {issue['html_url']} — skipping duplicate.")
            return

    r = requests.post(
        f"{GH_API}/repos/{repo}/issues",
        json={"title": title, "body": body, "labels": ["fee-check"]},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=TIMEOUT,
    )
    if r.status_code == 201:
        print(f"Issue created: {r.json()['html_url']}")
    else:
        print(f"Failed to create issue: {r.status_code}\n{r.text}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    with open(FEES_JSON) as f:
        current = json.load(f)

    print(f"fees.json edition : {current.get('edition')}")
    print(f"fees.json verified: {current.get('verified')}")

    # Download G-1055
    print(f"\nDownloading G-1055 from {G1055_URL} ...")
    try:
        pdf_bytes = download_pdf(G1055_URL)
        print(f"Download OK ({len(pdf_bytes):,} bytes).")
    except Exception as e:
        save_status(current, "check_failed", f"G-1055 download failed: {e}")
        open_issue(
            "⚠️ Fee Check: G-1055 download failed",
            f"## G-1055 could not be downloaded\n\n"
            f"**Error:** `{e}`\n\n"
            f"**Action required:** Manually verify fees at {G1055_URL}\n\n"
            f"---\n_Checked: {now_utc()} — fees.json edition: {current.get('edition')}_",
        )
        sys.exit(0)

    # Send to Claude for extraction
    print(f"Sending PDF to Claude ({MODEL}) for fee extraction ...")
    try:
        extracted = extract_fees_with_claude(pdf_bytes)
        print(f"Extraction OK. Live edition: {extracted.get('edition')}")
    except json.JSONDecodeError as e:
        save_status(current, "check_failed", f"Claude output unparseable: {e}")
        open_issue(
            "⚠️ Fee Check: Claude returned unexpected output",
            f"## Claude could not parse the G-1055\n\n"
            f"**Error:** `{e}`\n\n"
            f"This may indicate the PDF is scanned/image-only or the form layout has changed significantly.\n\n"
            f"**Action required:** Manually verify fees at {G1055_URL}\n\n"
            f"---\n_Checked: {now_utc()} — fees.json edition: {current.get('edition')}_",
        )
        sys.exit(0)
    except Exception as e:
        save_status(current, "check_failed", f"Claude API call failed: {e}")
        open_issue(
            "⚠️ Fee Check: Claude API call failed",
            f"## Could not contact Claude API\n\n"
            f"**Error:** `{e}`\n\n"
            f"**Action required:** Check ANTHROPIC_API_KEY secret is set in GitHub Actions, then re-run the workflow.\n\n"
            f"---\n_Checked: {now_utc()}_",
        )
        sys.exit(0)

    # Compare
    diffs = diff_fees(current, extracted)

    if not diffs:
        # Success — bump verified date and mark status as ok.
        save_status(current, "ok", "", bump_verified=True)
        print(f"\n✓ All fees match fees.json (edition {current.get('edition')}). No action needed.")
        sys.exit(0)

    # Differences found — mark status and open issue
    summary = f"{len(diffs)} fee change(s) detected; verify against the live G-1055."
    save_status(current, "changes_detected", summary)

    print(f"\n! {len(diffs)} difference(s) detected:")
    for d in diffs:
        print(f"  • {d}")

    diff_table = "\n".join(f"| {d.split(':')[0]} | {':'.join(d.split(':')[1:])} |" for d in diffs)

    open_issue(
        f"🚨 Fee Check: G-1055 fees have changed (live edition {extracted.get('edition')})",
        f"## USCIS G-1055 fee changes detected by Claude\n\n"
        f"| Field | Change |\n|---|---|\n"
        f"| **Current fees.json edition** | `{current.get('edition')}` |\n"
        f"| **Live G-1055 edition** | `{extracted.get('edition')}` |\n"
        f"| **Detected** | {now_utc()} |\n\n"
        f"---\n\n"
        f"## Detected differences\n\n"
        f"| Fee | Change |\n|---|---|\n"
        f"{diff_table}\n\n"
        f"---\n\n"
        f"## Steps to resolve\n\n"
        f"1. Open the live G-1055: {G1055_URL}\n"
        f"2. Verify each changed fee above against the PDF\n"
        f"3. Update `fees.json` — change `edition`, `verified`, and any fee amounts\n"
        f"4. Commit and push — the live app picks up changes automatically\n"
        f"5. Close this issue\n\n"
        f"---\n_Claude model used: `{MODEL}` — fees extracted directly from PDF, not regex._\n"
        f"_This issue was opened automatically by the fee-check workflow._",
    )


if __name__ == "__main__":
    main()
