#!/usr/bin/env python3
"""
check_fees.py

Downloads the USCIS G-1055 PDF, sends it to Claude for intelligent fee
extraction, compares extracted values against fees.json, and opens a GitHub
Issue automatically if anything has changed or cannot be verified.

Run manually:  ANTHROPIC_API_KEY=sk-... python scripts/check_fees.py
Run via CI:    see .github/workflows/check-fees.yml
"""

import base64
import io
import json
import os
import sys
from datetime import datetime, timezone

import anthropic
import requests

# ── Config ────────────────────────────────────────────────────────────────────

G1055_URL = "https://www.uscis.gov/sites/default/files/document/forms/g-1055.pdf"
FEES_JSON  = os.path.join(os.path.dirname(__file__), "..", "fees.json")
GH_API     = "https://api.github.com"
TIMEOUT    = 30
MODEL      = "claude-haiku-4-5"   # fast, cheap — ideal for structured extraction

# The schema we ask Claude to fill in — mirrors fees.json exactly so comparison is direct.
EXTRACTION_PROMPT = """
You are reviewing a USCIS Form G-1055 fee schedule PDF.

Extract the following fee values and return ONLY a JSON object with this exact structure
(use integer dollar amounts, no $ signs or commas):

{
  "edition": "MM/DD/YY as printed on the form",
  "pp_fee": <I-907 Premium Processing Fee amount>,
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

Key mappings:
- "large" = Regular employer (more than 25 employees)
- "small" = Small employer or nonprofit (25 or fewer employees)
- "named" = Named beneficiaries (up to 25 per petition)
- "unnamed" = Unnamed beneficiaries
- "filing" = I-129 filing fee
- "fraud" = Fraud Prevention and Detection Fee (H-2B only)
- "asylum" = Asylum Program Fee
- "pp_fee" = I-907 Premium Processing Fee

Return ONLY the JSON object, no explanation or markdown.
""".strip()

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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
        max_tokens=1024,
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
    return json.loads(text)


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
    repo  = os.environ.get("GITHUB_REPOSITORY")

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
        print(f"\n✓ All fees match fees.json (edition {current.get('edition')}). No action needed.")
        sys.exit(0)

    # Differences found — open issue
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
