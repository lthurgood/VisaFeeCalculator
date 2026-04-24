# H-2A / H-2B USCIS Fee Calculator

A lightweight, self-contained fee calculator for H-2A and H-2B visa petitions. Built for quick reference and easy sharing — no backend, no dependencies, no login required.

## What it does

Walks users through a simple step-by-step selection to determine the correct USCIS paper filing fees for an H-2A or H-2B petition, including:

- **Visa type** — H-2A (agricultural) or H-2B (non-agricultural)
- **Premium processing** — I-907 election for H-2B petitions
- **Worker naming** — named (up to 25 per petition) or unnamed (no limit)
- **Worker count** — for named beneficiaries, calculates petitions required if count exceeds 25 (per 8 C.F.R. § 214.2(h)(2)(ii))
- **Employer size** — regular (more than 25 employees) or small/nonprofit (25 or fewer)

Once all selections are made, the calculator displays an itemized fee breakdown and estimated total.

## Audit trail & PDF report

Every session requires an employer name and preparer name before calculations unlock. Clicking **Download / Print PDF Report** generates a clean one-page audit report containing:

- Auto-generated reference ID (format: `VFC-YYYYMMDD-XXXXX`)
- Timestamp and preparer information
- All selections made
- Itemized fee breakdown and total
- Applicable filing notes and disclaimer

The PDF filename is automatically set to the reference ID and employer name (e.g. `VFC-20260424-AB12C - Sunrise Farms LLC.pdf`).

## Fees included

| Fee | Applies to |
|---|---|
| I-129 filing fee | All petitions |
| Fraud Prevention & Detection Fee ($150) | H-2B only, not waivable |
| I-907 Premium Processing Fee ($1,780) | H-2B only, if selected |
| Asylum Program Fee | All petitions, paid separately |

> All fees reflect **paper filing** amounts from USCIS Form G-1055. The current edition and last confirmed date are displayed in the app header.

## Fee data & automated monitoring

Fees are stored in `fees.json` and loaded dynamically. If the file cannot be fetched (e.g. opened locally), the app falls back to built-in values and displays a warning banner.

A GitHub Actions workflow runs every Monday at 9:00 AM UTC. It:

1. Downloads the live USCIS G-1055 PDF
2. Sends it to Claude AI (claude-haiku-4-5) for intelligent fee extraction
3. Compares extracted values against `fees.json`
4. Opens a GitHub Issue automatically if any fees have changed or cannot be verified

This requires an `ANTHROPIC_API_KEY` secret set in the repository's GitHub Actions secrets.

## Hosting

The calculator is a single HTML file with no external dependencies. Hosted via GitHub Pages — no server required.

## Usage notes

- H-2A petitions are not currently designated for premium processing — the calculator notes this if H-2A is selected.
- Nonprofit petitioners qualify for the small employer filing fee and pay $0 in Asylum Program Fees.
- The I-907 Premium Processing Fee must always be submitted separately from other filing fees.
- Fees adjust periodically. Always verify current fees at [uscis.gov/g-1055](https://www.uscis.gov/g-1055) before filing.

## Source

USCIS Form G-1055 — [uscis.gov/g-1055](https://www.uscis.gov/g-1055)

## License

MIT License

Copyright (c) 2026 Inukshuk, LLC

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
