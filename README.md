# HisabSmart

HisabSmart is a polished personal finance dashboard starter built with Flask, SQLite, vanilla HTML/CSS/JavaScript, and Chart.js.

## Features

- Responsive sidebar navigation that becomes a mobile drawer
- Premium fintech visual system with porcelain, charcoal, white, and neon-orange colors
- Real database-backed income, expense, cash-flow, budget, and inventory cards
- Daily, weekly, monthly, yearly, and custom date filters
- Chart.js spending trend and category-wise donut charts
- Recent filtered transactions and budget progress cards
- SQLite database initialized with useful demo data
- Small `/api/overview` endpoint for dashboard totals
- User registration, login, logout, secure password hashing, server-side validation, and CSRF protection
- Per-user ownership on transactions, goods, budgets, receipts, recurring expenses, savings goals, and shared wallets
- Full transaction management: create, edit, delete, search, sort, filter, custom categories, tags, payment methods, and receipt uploads
- Personal goods inventory with photos, purchase details, warranties, receipts, estimated value, product cards, filters, sorting, details, and ownership-safe CRUD
- Weekly, monthly, yearly, and category-specific budgets calculated from actual transactions
- Rollover budgets with 50%, 80%, and 100% utilization alerts
- 50/30/20 spending guidance, savings goals, emergency funds, and upcoming bill reminders
- Receipt scanning with isolated Tesseract OCR extraction, editable review, duplicate protection, and optional inventory item creation
- Analytics and reports with period filters, category/vendor insights, comparisons, and authenticated CSV, Excel, and PDF exports
- Recurring bills and subscriptions with weekly, monthly, quarterly, and yearly schedules, pause/resume/cancel controls, payment history, duplicate warnings, and price-increase detection
- Private AI financial assistant with authenticated spending answers, affordability checks, anomaly detection, duplicate alerts, budget-overrun predictions, and month-end cash-flow estimates
- Shared wallets for trips, roommates, and households with invitations, view-only/editor access, equal or proportional bill splits, balances, debt suggestions, and offline settlement tracking

## Run locally

1. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Configure the environment:

   ```powershell
   Copy-Item .env.example .env
   $env:HISABSMART_SECRET_KEY = "replace-with-a-long-random-production-secret"
   ```

   Flask does not load `.env` automatically in this application, so set variables in the shell, your process manager, or deployment environment. Use `HISABSMART_SESSION_COOKIE_SECURE=true` behind HTTPS. Set `ENABLE_SCHEDULER=false` for test processes or when another worker owns recurring-payment scheduling.

4. Initialize the database:

   ```powershell
   flask --app app init-db
   ```

   Initialization is also performed automatically when the application starts. Existing databases are migrated without replacing user records.

5. Start the app:

   ```powershell
   python run.py
   ```

6. Open <http://127.0.0.1:5000> in your browser.

The first run creates `hisabsmart.db`. Dashboard values are calculated from the signed-in user's SQLite records. You can also run `flask --app app init-db` to initialize it explicitly.

Set `HISABSMART_SECRET_KEY` to a long random value before deploying. All POST forms require a server-generated CSRF token, and every financial query is scoped to the authenticated user's ID.

Budget usage is calculated against calendar periods: weekly budgets run Monday through Sunday, monthly budgets run from the first through the last day of the month, and yearly budgets run from January 1 through December 31. Rollover carries unused prior-period allowance into the current period only when enabled. Savings goals and recurring bill reminders are persisted per user.

## Receipt OCR

Install the Python dependencies with `pip install -r requirements.txt` and install the Tesseract OCR engine separately for your operating system. The app uses `pytesseract` and Pillow through the isolated [receipt_ocr.py](./receipt_ocr.py) service. If Tesseract is missing, the scan page reports a clear setup error instead of creating partial financial data. OCR results are always editable on the review screen before a transaction is saved.

Receipt uploads accept PDF, PNG, JPG, JPEG, and WEBP files up to 5 MB. Files are stored with generated names under `instance/uploads/receipts` and are only downloadable through an ownership-checked route.

## Analytics and recurring payments

Open **Analytics & reports** to review weekly, monthly, yearly, or custom-period income, expenses, cash flow, savings rate, category breakdowns, vendors, largest purchases, and prior-period/year comparisons. CSV, Excel, and PDF downloads use the same authenticated date range and never include another user's records.

Open **Recurring payments** to create a subscription or bill schedule. The background scheduler creates at most one transaction per payment and due date using a database uniqueness constraint, advances missed schedules, and records payment history. Use `ENABLE_SCHEDULER=false` in tests or one-off scripts when the background worker is not wanted.

## AI financial assistant

Open **AI assistant** to ask questions about your finances. The assistant calculates answers from the signed-in user's SQLite records on the server, including current-month spending, biggest expenses, affordability checks, duplicate transaction groups, unusual expenses, budget-overrun risks, and estimated month-end cash flow. Saved transactions are never sent to a browser-side model or third-party service, and every assistant request is protected by the login session and CSRF validation.

Responses label saved-record answers as **Actual data** and pace-based calculations as **Prediction**. Predictions are estimates rather than guarantees. If an assistant calculation fails, the API returns a safe error without modifying financial records.

## Shared wallets

Open **Shared wallets** to create a wallet for a trip, roommates, or a household. Wallet owners can add existing HisabSmart users as editors or view-only members. Editors can record shared expenses and settlements; view-only members can inspect balances without changing records. Expenses support equal or proportional splits, and balances calculate who owes whom. Settlement records are bookkeeping entries only and never initiate money transfers. Every wallet, member, expense, split, and settlement query is scoped through authenticated membership checks.

Goods photos accept valid PNG, JPG, JPEG, and WEBP images. Goods receipts accept valid PDFs or images. Uploads are stored under `instance/uploads/goods`, use generated filenames, and are served only after checking the signed-in user's ownership.

Uploads are limited to 5 MB and use generated server-side filenames. Goods and OCR uploads are checked against file signatures as well as extensions; transaction receipt attachments are restricted by extension and size. Never expose the upload directories as static web folders.

## Test authentication and isolation

```powershell
pytest
```

## Project structure

```text
.
├── app.py
├── auth.py
├── db.py
├── routes.py
├── financial_assistant.py
├── scheduler.py
├── requirements.txt
├── .env.example
├── README.md
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── transaction_form.html
│   ├── transactions.html
│   ├── goods.html
│   ├── good_form.html
│   ├── good_detail.html
│   ├── reports.html
│   ├── recurring.html
│   ├── assistant.html
│   ├── wallets.html
│   └── wallet_detail.html
└── static/
    ├── css/style.css
    └── js/app.js
└── tests/
    ├── test_auth.py
    ├── test_transactions.py
    ├── test_goods.py
    ├── test_budgets.py
    ├── test_receipt_ocr.py
    ├── test_assistant.py
    └── test_wallets.py
```
