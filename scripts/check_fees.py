#!/usr/bin/env python3
"""
check_fees.py

Downloads the USCIS G-1055 fee schedule PDF, extracts the edition date and
all dollar amounts, then compares against fees.json. Opens a GitHub Issue
automatically if the edition has changed or if the download/parse fails.

Run manually:  python scripts/check_fees.py
Run via CI:    see .github/workflows/check-fees.yml
"""

import io
import json
import os
import re
import sys
from datetime import datetime, timezone

import pdfplumber
import requests

# ── Config ────────────────────────────────────────────────────────────────────

G1055_URL  = "https://www.uscis.gov/sites/default/files/document/forms/g-1055.pdf"
FEES_JSON  = os.path.join(os.path.dirname(__file__), "..", "fees.json")
GH_API     = "https://api.github.com"
TIMEOUT    = 30

# Fee labels we care about — used to produce a human-readable diff in the issue.
FEE_LABELS = {
    "h2a.named.large.filing":   "H-2A Named  / Regular    / I-129 filing",
    "h2a.named.small.filing":   "H-2A Named  / Small-NP   / I-129 filing",
    "h2a.unnamed.large.filing": "H-2A Unnamed / Regular   / I-129 filing",
    "h2a.unnamed.small.filing": "H-2A Unnamed / Small-NP  / I-129 filing",
    "h2b.named.large.filing":   "H-2B Named  / Regular    / I-129 filing",
    "h2b.named.small.filing":   "H-2B Named  / Small-NP   / I-129 filing",
    "h2b.unnamed.large.filing": "H-2B Unnamed / Regular   / I-129 filing",
    "h2b.unnamed.small.filing": "H-2B Unnamed / Small-NP  / I-129 filing",
    "h2b.named.large.fraud":    "H-2B Fraud Prevention & Detection Fee",
    "h2a.named.large.asylum":   "Asylum Program Fee (Regular employer)",
    "h2a.named.small.asylum":   "Asylum Program Fee (Small/Nonprofit)",
    "pp_fee":                   "I-907 Premium Processing Fee",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def download_pdf(url: str) -> io.BytesIO:
    r = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "VisaFeeCalculator-FeeChecker/1.0"},
        allow_redirects=True,
    )
    r.raise_for_status()
    return io.BytesIO(r.content)


def extract_text(pdf_bytes: io.BytesIO) -> str:
    text = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text.append(t)
    return "\n".join(text)


def find_edition(text: str) -> str | None:
    """Looks for 'Edition MM/DD/YY' anywhere in the PDF text."""
    m = re.search(r"Edition\s+(\d{2}/\d{2}/\d{2})", text, re.IGNORECASE)
    return m.group(1) if m else None


def find_dollar_amounts(text: str) -> list[int]:
    """Returns all unique dollar amounts found in the text, sorted."""
    raw = re.findall(r"\$\s*([\d,]+)", text)
    amounts = sorted({int(v.replace(",", "")) for v in raw})
    return amounts


def flat_fees(data: dict) -> dict[str, int]:
    """Flatten fees.json into dotted-key → amount for easy comparison."""
    result = {"pp_fee": data["pp_fee"]}
    for visa, workers_map in data["fees"].items():
        for workers, size_map in workers_map.items():
            for size, components in size_map.items():
                for component, amount in components.items():
                    result[f"{visa}.{workers}.{size}.{component}"] = amount
    return result


# ── GitHub Issue ──────────────────────────────────────────────────────────────

def open_issue(title: str, body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo  = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print("GITHUB_TOKEN / GITHUB_REPOSITORY not set — printing issue instead.\n")
        print(f"TITLE: {title}\n\nBODY:\n{body}")
        return

    # Avoid duplicate open issues with the same title.
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
    # Load current fees.json
    with open(FEES_JSON) as f:
        current = json.load(f)

    current_edition = current.get("edition", "unknown")
    print(f"fees.json edition : {current_edition}")
    print(f"fees.json verified: {current.get('verified')}")

    # Download G-1055
    print(f"\nDownloading G-1055 from {G1055_URL} ...")
    try:
        pdf_bytes = download_pdf(G1055_URL)
        print("Download OK.")
    except Exception as e:
        open_issue(
            "⚠️ Fee Check: G-1055 download failed",
            f"## G-1055 could not be downloaded\n\n"
            f"**Error:** `{e}`\n\n"
            f"**Action required:** Manually verify fees at {G1055_URL}\n\n"
            f"---\n_Checked: {now_utc()} — fees.json edition: {current_edition}_",
        )
        sys.exit(0)

    # Parse PDF
    print("Parsing PDF text ...")
    try:
        text = extract_text(pdf_bytes)
    except Exception as e:
        open_issue(
            "⚠️ Fee Check: G-1055 could not be parsed",
            f"## G-1055 downloaded but PDF parsing failed\n\n"
            f"**Error:** `{e}`\n\n"
            f"The PDF structure may have changed. Manual review required.\n\n"
            f"**Action required:** Manually verify fees at {G1055_URL}\n\n"
            f"---\n_Checked: {now_utc()} — fees.json edition: {current_edition}_",
        )
        sys.exit(0)

    live_edition = find_edition(text)
    amounts      = find_dollar_amounts(text)
    amounts_str  = "  ".join(f"${a:,}" for a in amounts)

    print(f"Live G-1055 edition: {live_edition or '(not found)'}")
    print(f"Dollar amounts found: {amounts_str or '(none)'}")

    # ── Case 1: edition date not found in PDF ──────────────────────────────
    if not live_edition:
        open_issue(
            "⚠️ Fee Check: Edition date not found in G-1055",
            f"## Edition date could not be detected\n\n"
            f"The G-1055 was downloaded and parsed, but no edition date "
            f"matching `Edition MM/DD/YY` was found in the text. "
            f"The PDF layout may have changed.\n\n"
            f"**Dollar amounts found in PDF:**\n`{amounts_str}`\n\n"
            f"**Action required:** Manually verify fees at {G1055_URL}\n\n"
            f"---\n_Checked: {now_utc()} — fees.json edition: {current_edition}_",
        )
        sys.exit(0)

    # ── Case 2: edition unchanged ──────────────────────────────────────────
    if live_edition == current_edition:
        print(f"\n✓ Edition unchanged ({live_edition}). No action needed.")
        sys.exit(0)

    # ── Case 3: new edition detected ───────────────────────────────────────
    print(f"\n! New edition detected: {live_edition} (was {current_edition})")

    current_flat = flat_fees(current)
    fee_table_rows = []
    for key, label in FEE_LABELS.items():
        current_val = current_flat.get(key)
        fee_table_rows.append(
            f"| {label} | ${current_val:,} if current_val is not None else 'N/A' | _verify_ |"
        )
    fee_table = "\n".join(fee_table_rows)

    open_issue(
        f"🚨 Fee Check: New G-1055 edition detected ({live_edition})",
        f"## USCIS G-1055 has been updated\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Previous edition | `{current_edition}` |\n"
        f"| **New edition** | **`{live_edition}`** |\n"
        f"| Detected | {now_utc()} |\n\n"
        f"---\n\n"
        f"## Current fees in fees.json (verify each against new PDF)\n\n"
        f"| Fee | Current amount | New amount |\n|---|---|---|\n"
        f"{fee_table}\n\n"
        f"---\n\n"
        f"## Dollar amounts found in new G-1055 PDF\n\n"
        f"`{amounts_str}`\n\n"
        f"---\n\n"
        f"## Steps to resolve\n\n"
        f"1. Open the new G-1055: {G1055_URL}\n"
        f"2. Compare each fee row above against the new PDF\n"
        f"3. Update `fees.json` — change `edition`, `verified`, and any fee amounts that changed\n"
        f"4. Commit and push — the live app picks up changes automatically\n"
        f"5. Close this issue\n\n"
        f"---\n_This issue was opened automatically by the fee-check workflow._",
    )


if __name__ == "__main__":
    main()
