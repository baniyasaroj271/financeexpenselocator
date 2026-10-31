<div align="center">

# HisabSmart

### A clearer view of your money, every day.

An all-in-one personal finance workspace for tracking spending, planning ahead,
managing possessions, and sharing expenses with the people you trust.

</div>

---

## What is HisabSmart?

HisabSmart is a private, full-stack personal finance application built with
Flask and SQLite. It turns everyday financial records into useful dashboards,
budgets, reports, reminders, savings plans, and practical insights.

The app is designed around one principle: **your financial records should stay
under your control**. Data is stored in the application's database, financial
queries are scoped to the signed-in user, and the financial assistant processes
answers from that user's records on the server.

## Why use it?

- See income, expenses, cash flow, and budgets in one dashboard.
- Understand spending patterns with charts and date-based filters.
- Plan for bills, savings goals, and emergency funds.
- Keep receipts and personal goods in an organized inventory.
- Track subscriptions before they become forgotten expenses.
- Split shared costs with roommates, friends, or household members.
- Ask the built-in assistant questions about your own financial activity.

## Features

### Dashboard and transactions

- Responsive fintech dashboard with mobile navigation.
- Daily, weekly, monthly, yearly, and custom date ranges.
- Income, expense, cash-flow, and savings-rate summaries.
- Spending trend and category charts powered by Chart.js.
- Transaction search, sorting, filters, categories, tags, payment methods,
  editing, deletion, and receipt attachments.
- Small authenticated `/api/overview` endpoint for dashboard totals.

### Budgets and savings

- Weekly, monthly, yearly, and category-specific budgets.
- Budget calculations based on real transactions.
- Optional rollover of unused budget allowance.
- 50%, 80%, and 100% utilization alerts.
- 50/30/20 spending guidance.
- Savings goals, emergency funds, and upcoming bill reminders.

### Reports and exports

- Analytics for income, expenses, cash flow, savings rate, categories, vendors,
  largest purchases, and comparisons.
- Weekly, monthly, yearly, and custom reporting periods.
- Authenticated CSV, Excel, and PDF exports.
- Exports use the same user and date-range restrictions as the report screen.

### Recurring payments

- Weekly, monthly, quarterly, and yearly schedules.
- Pause, resume, and cancel controls.
- Payment history and missed-schedule handling.
- Duplicate warnings and price-increase detection.
- Idempotent background processing so a payment is not recorded twice for the
  same due date.

### Receipt OCR and goods inventory

- Scan PDF and image receipts with optional Tesseract OCR.
- Review and edit extracted data before saving a transaction.
- Duplicate protection for scanned receipts.
- Add an inventory item directly from a reviewed receipt.
- Track personal goods with photos, purchase details, warranties, receipts,
  estimated value, filters, sorting, and ownership-safe CRUD.

### Shared wallets

- Create wallets for trips, roommates, or households.
- Invite existing HisabSmart users as editors or view-only members.
- Record shared expenses and settlements.
- Split bills equally or proportionally.
- Calculate balances and suggest who owes whom.
- Keep offline settlement records without initiating money transfers.

### Private financial assistant

The assistant answers questions using the signed-in user's saved records,
including:

- Current-month spending and biggest expenses
- Affordability checks
- Duplicate transaction groups
- Unusual-expense detection
- Budget-overrun risks
- Estimated month-end cash flow

Responses distinguish **Actual data** from **Prediction**. Predictions are
estimates, not guarantees. Financial records are not sent to a browser-side
model or third-party AI service.

## Technology

- **Backend:** Python, Flask
- **Database:** SQLite
- **Frontend:** Jinja templates, HTML, CSS, vanilla JavaScript
- **Charts:** Chart.js
- **OCR:** Tesseract, pytesseract, and Pillow
- **Exports:** openpyxl and ReportLab
- **Scheduling:** Python background scheduler
- **Testing:** pytest

## Quick start

### Requirements

- Python 3.11 or newer
- Tesseract OCR only if you want receipt scanning

### 1. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure the application

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

The application reads configuration from environment variables. Set a long,
random secret before using the app beyond local development:

```powershell
$env:HISABSMART_SECRET_KEY = "replace-with-a-long-random-production-secret"
```

The application does not load `.env` automatically. Set variables in your
shell, IDE, process manager, or deployment environment.

### 4. Initialize the database

Database initialization happens automatically when the app starts. You can also
run it explicitly:

```powershell
flask --app app init-db
```

Existing development databases are migrated without replacing user records.

### 5. Start HisabSmart

```powershell
python run.py
```

Open <http://127.0.0.1:5000> in your browser.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `HISABSMART_SECRET_KEY` | Session and CSRF signing key | Local development fallback |
| `HISABSMART_SESSION_COOKIE_SECURE` | Marks cookies as HTTPS-only | Disabled |
| `ENABLE_SCHEDULER` | Enables recurring-payment processing | `true` |
| `HISABSMART_HOST` | Development server host | `127.0.0.1` |
| `HISABSMART_PORT` | Development server port | `5000` |
| `HISABSMART_DEBUG` | Enables Flask debug mode | `false` |

Set `HISABSMART_SESSION_COOKIE_SECURE=true` when serving through HTTPS. Set
`ENABLE_SCHEDULER=false` for tests, one-off scripts, or deployments where a
different worker owns recurring-payment processing.

## Optional receipt OCR

The Python OCR dependencies are included in `requirements.txt`, but the
Tesseract system binary must be installed separately.

1. Install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) for your
   operating system.
2. Make sure `tesseract` is available on `PATH`.
3. If it is installed elsewhere, configure its executable path in the OCR
   service or deployment environment.

OCR results are always editable before a transaction is saved. If Tesseract is
missing, the scan page reports a setup error instead of creating partial
financial data.

Supported receipt uploads include PDF, PNG, JPG, JPEG, and WEBP files up to
5 MB. Uploads use generated filenames and are served only through
ownership-checked routes.

## Privacy and security

- Registration, login, and logout are built into the application.
- Passwords are securely hashed.
- POST forms require server-generated CSRF tokens.
- Financial queries are scoped to the authenticated user's ID.
- Shared-wallet data requires authenticated wallet membership.
- Uploads are limited in size and stored outside public static directories.
- Goods and OCR files are checked against file signatures as well as extensions.
- Session cookies are HTTP-only and SameSite Lax.
- Settlement entries are bookkeeping records; they never transfer money.

Do not commit real secrets, production databases, or private receipt uploads.
For production, use HTTPS, a strong secret key, a managed process/server setup,
and an appropriate backup strategy.

## Project structure

```text
.
├── app.py                  # Flask application factory and configuration
├── auth.py                 # Registration, login, logout, and CSRF helpers
├── db.py                   # SQLite connection, schema, and migrations
├── routes.py               # Dashboard and feature routes
├── financial_assistant.py  # Private financial analysis service
├── receipt_ocr.py          # Receipt OCR service
├── scheduler.py            # Recurring-payment scheduler
├── run.py                  # Local development entry point
├── requirements.txt        # Python dependencies
├── .env.example            # Environment configuration template
├── templates/              # Jinja pages and feature views
├── static/                 # CSS, JavaScript, and frontend assets
└── tests/                  # Authentication, isolation, and feature tests
```

## Testing

Run the complete test suite from the repository root:

```powershell
pytest
```

The tests cover authentication, user isolation, transactions, budgets, goods,
receipt OCR, reports, recurring payments, the financial assistant, and shared
wallets.

## Development notes

- The included SQLite database and demo records are intended for local
  development.
- Keep recurring-payment scheduling enabled in only one process per deployment.
- Use `ENABLE_SCHEDULER=false` for test processes.
- Never expose `instance/uploads` as a public static directory.
- Treat assistant predictions as estimates rather than financial advice.

---

<div align="center">

Built to make everyday money management simpler, more private, and easier to
understand.

</div>
