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

3. Start the app:

   ```powershell
   python app.py
   ```

4. Open <http://127.0.0.1:5000> in your browser.

The first run creates `hisabsmart.db`. Dashboard values are calculated from the signed-in user's SQLite records. You can also run `flask --app app init-db` to initialize it explicitly.

Set `HISABSMART_SECRET_KEY` to a long random value before deploying. All POST forms require a server-generated CSRF token, and every financial query is scoped to the authenticated user's ID.

Receipt uploads accept PDF, PNG, JPG, JPEG, and WEBP files up to 5 MB. Files are stored with generated names under `instance/uploads/receipts` and are only downloadable through an ownership-checked route.

Goods photos accept valid PNG, JPG, JPEG, and WEBP images. Goods receipts accept valid PDFs or images. Uploads are stored under `instance/uploads/goods`, use generated filenames, and are served only after checking the signed-in user's ownership.

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
├── requirements.txt
├── README.md
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── transaction_form.html
│   ├── transactions.html
│   ├── goods.html
│   ├── good_form.html
│   └── good_detail.html
└── static/
    ├── css/style.css
    └── js/app.js
└── tests/
    ├── test_auth.py
    ├── test_transactions.py
    └── test_goods.py
```
# financeexpenselocator
