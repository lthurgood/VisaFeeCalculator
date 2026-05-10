# H-2A / H-2B USCIS Fee Calculator

A single-page web calculator for USCIS paper-filing fees on H-2A (agricultural) and H-2B (non-agricultural) visa petitions, with an automated three-model verification system that monitors the live USCIS Form G-1055 and alerts on changes.

🔗 **Live calculator:** https://lthurgood.github.io/VisaFeeCalculator/

---

## What it does

Enter the visa type, named vs. unnamed beneficiaries, employer size, and (for H-2B) whether you want premium processing. The calculator computes:

- **I-129 filing fee** — varies by visa, beneficiary type, and employer size
- **Fraud Prevention & Detection Fee** — H-2B only, flat $150
- **I-907 Premium Processing Fee** — optional for H-2B, $1,780
- **Asylum Program Fee** — $0 / $300 / $600 by employer size
- **Multi-petition handling** — automatically splits >25 named workers into separate I-129 petitions per 8 C.F.R. § 214.2(h)(2)(ii)

Outputs a printable PDF audit report with reference ID, employer name, preparer name, and timestamp suitable for filing documentation.

---

## How the fee data stays current

The fees are sourced from USCIS Form G-1055, which USCIS revises periodically (a few times per year on average). Rather than manually monitor for changes, this repo runs an automated verification system:

### Twice-daily verification

A GitHub Actions cron runs at 9 AM and 3 PM Pacific. Each run:

1. Downloads the live USCIS G-1055 PDF.
2. SHA-256 hashes the bytes and compares against the last known hash.
3. **Hash matches** → updates the "last checked" timestamp, exits in ~3 seconds. Costs $0.
4. **Hash differs** → invokes three Claude models in parallel (Haiku 4.5, Sonnet 4.6, Opus 4.7), extracts the fees from the PDF, takes majority vote per field, diffs against the current `fees.json`, and routes to one of four outcomes:

| Outcome | What happens |
|---|---|
| All 3 agree, no diff | Updates verified date silently |
| All 3 agree, edition-only change | Auto-applies new edition silently |
| All 3 agree, real fee diff | Opens a high-confidence GitHub issue with diff table |
| Models disagree on any field | Opens a low-confidence issue with side-by-side comparison |

### Cost

Hash check runs cost $0. Three-model consensus only fires when USCIS actually publishes a new edition — roughly 4–6 times per year at ~$1.20 per event. **Total: ~$5–7 per year.**

### Why three models

Single-model PDF extraction is unreliable for compliance-critical fees. The G-1055 lists fees for dozens of forms and classifications; a model can easily read the wrong row (e.g. report $2,965 for I-907 premium processing because it grabbed the I-129 H-1B/L/O fee instead of the H-2B fee at $1,780). Three-model majority voting catches single-model misreads. When models disagree, the script surfaces the disagreement instead of silently picking a wrong number.

### Notifications

GitHub issues opened by the bot fan out automatically to:
- **Slack** — via the GitHub Slack app subscribed to repo issues (`/github subscribe lthurgood/VisaFeeCalculator issues`)
- **Email** — via standard GitHub notification settings
- **Mobile push** — via the GitHub mobile app

Silent runs produce no noise. You only hear from it when there's a decision for a human to make.

---

## Calculator UI

The badge at the top of the calculator displays the current verification status:

> ✓ Fees verified · Ed. 05/06/26 · Checked May 8, 9:32 PM

Timestamps render in the visitor's local timezone. The badge turns amber and a warning banner appears if a change is detected or the automated check has failed.

---

## Architecture

| File | Purpose |
|---|---|
| `index.html` | Single-page calculator + PDF report generator |
| `fees.json` | Fee data + status fields (consumed by the calculator) |
| `scripts/check_fees.py` | Hash-gated, three-model consensus verifier |
| `.github/workflows/check-fees.yml` | Twice-daily cron + commit step |
| `.fee_check_state.json` | Internal state (PDF hash, last-check timestamp) |

The `fees.json` file is intentionally user-facing — the calculator fetches it on page load and renders both the fee values and the verification status from it. The `.fee_check_state.json` file is internal state for the verifier and isn't read by the calculator.

---

## Running the verifier locally

```bash
pip install anthropic requests
ANTHROPIC_API_KEY=sk-... python scripts/check_fees.py
```

Without `GITHUB_TOKEN` and `GITHUB_REPOSITORY` env vars set, the script prints what it would do to an issue instead of creating one. Useful for testing prompts and routing logic without touching the live repo.

---

## Disclaimer

This calculator is informational. Always verify current fees directly against the live USCIS Form G-1055 at https://www.uscis.gov/g-1055 before submitting any petition. This tool does not constitute legal advice — consult a licensed immigration attorney for guidance specific to your situation.

---

## License

MIT. © 2026 Inukshuk, LLC.
