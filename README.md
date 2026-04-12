# H-2A / H-2B USCIS Fee Calculator

A lightweight, self-contained fee calculator for H-2A and H-2B visa petitions. Built for quick reference and easy sharing — no backend, no dependencies, no login required.

## What it does

Walks users through a simple step-by-step selection to determine the correct USCIS paper filing fees for an H-2A or H-2B petition, including:

- **Visa type** — H-2A (agricultural) or H-2B (non-agricultural)
- **Premium processing** — I-907 election for H-2B petitions
- **Worker naming** — named (up to 25 per petition) or unnamed (no limit)
- **Employer size** — regular (more than 25 employees) or small/nonprofit (25 or fewer)

Once all selections are made, the calculator displays an itemized fee breakdown and estimated total.

## Fees included

| Fee | Applies to |
|---|---|
| I-129 filing fee | All petitions |
| Fraud Prevention & Detection Fee ($150) | H-2B only, not waivable |
| I-907 Premium Processing Fee ($1,780) | H-2B only, if selected |
| Asylum Program Fee | All petitions, paid separately |

> All fees reflect **paper filing** amounts from USCIS Form G-1055, Edition 03/23/26.

## Hosting

The calculator is a single HTML file with no external dependencies. Host it anywhere static files are served

## Usage notes

- H-2A petitions are not currently designated for premium processing — the calculator notes this if H-2A is selected.
- Nonprofit petitioners qualify for the small employer filing fee and pay $0 in Asylum Program Fees.
- The I-907 Premium Processing Fee must always be submitted separately from other filing fees.
- Fees adjust periodically. Always verify current fees at [uscis.gov](https://www.uscis.gov) before filing.

## Source

USCIS Form G-1055, Edition 03/23/26  
[uscis.gov/forms](https://www.uscis.gov/forms)

## License

MIT License

Copyright (c) 2026 Inukshuk, LLC

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
