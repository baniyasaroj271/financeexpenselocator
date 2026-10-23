from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO, StringIO
import csv
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from auth import login_required
from db import get_db
from receipt_ocr import OCRInputError, OCRUnavailableError, extract_receipt
from financial_assistant import answer_question, build_insights


main_bp = Blueprint("main", __name__)
PAYMENT_METHODS = {"cash", "bank", "card", "digital_wallet"}
TRANSACTION_TYPES = {"income", "expense"}
SORT_FIELDS = {
    "date_desc": "transaction_date DESC, id DESC",
    "date_asc": "transaction_date ASC, id ASC",
    "amount_desc": "amount DESC, id DESC",
    "amount_asc": "amount ASC, id ASC",
    "description": "title COLLATE NOCASE ASC, id ASC",
}
ALLOWED_RECEIPT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
OCR_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
GOODS_SORT_FIELDS = {
    "newest": "purchase_date DESC, id DESC",
    "oldest": "purchase_date ASC, id ASC",
    "name": "name COLLATE NOCASE ASC, id ASC",
    "purchase_high": "unit_price * quantity DESC, id DESC",
    "value_high": "estimated_value * quantity DESC, id DESC",
}
BUDGET_PERIODS = {"weekly", "monthly", "yearly"}
GOAL_TYPES = {"savings", "emergency"}
WALLET_ROLES = {"viewer", "editor"}
SPLIT_METHODS = {"equal", "proportional"}


def date_window(period: str, start_value: str | None, end_value: str | None) -> tuple[str, str]:
    today = date.today()
    if period == "custom":
        try:
            start = date.fromisoformat(start_value or "")
            end = date.fromisoformat(end_value or "")
        except ValueError:
            start = today - timedelta(days=30)
            end = today
        if start > end:
            start, end = end, start
        return start.isoformat(), end.isoformat()
    days = {"daily": 0, "weekly": 6, "monthly": 29, "yearly": 364}.get(period, 29)
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def dashboard_data(period: str, start: str, end: str) -> dict:
    db = get_db()
    user_id = g.user["id"]
    transactions = db.execute(
        """
        SELECT title, category, amount, transaction_type, transaction_date
        FROM transactions
        WHERE user_id = ? AND transaction_date BETWEEN ? AND ?
        ORDER BY transaction_date DESC, id DESC
        """,
        (user_id, start, end),
    ).fetchall()
    totals = db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) AS expenses
        FROM transactions WHERE user_id = ? AND transaction_date BETWEEN ? AND ?
        """,
        (user_id, start, end),
    ).fetchone()
    budgets = budgeting_data()["budgets"]
    inventory = db.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS goods_count, COALESCE(SUM(quantity * unit_price), 0) AS purchase_value FROM goods WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    categories = db.execute(
        """
        SELECT category, ROUND(SUM(amount), 2) AS total
        FROM transactions
        WHERE user_id = ? AND transaction_type = 'expense'
          AND transaction_date BETWEEN ? AND ?
        GROUP BY category ORDER BY total DESC
        """,
        (user_id, start, end),
    ).fetchall()
    grouping = "substr(transaction_date, 1, 7)" if period == "yearly" else "transaction_date"
    trend = db.execute(
        f"""
        SELECT {grouping} AS bucket,
               ROUND(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 2) AS income,
               ROUND(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 2) AS expenses
        FROM transactions
        WHERE user_id = ? AND transaction_date BETWEEN ? AND ?
        GROUP BY bucket ORDER BY bucket
        """,
        (user_id, start, end),
    ).fetchall()
    budget_total = sum(row["effective_limit"] for row in budgets)
    budget_spent = sum(row["spent"] for row in budgets)
    income = float(totals["income"])
    expenses = float(totals["expenses"])
    return {
        "income": round(income, 2),
        "expenses": round(expenses, 2),
        "cash_flow": round(income - expenses, 2),
        "remaining_budget": round(max(budget_total - budget_spent, 0), 2),
        "goods_count": int(inventory["goods_count"]),
        "inventory_value": round(float(inventory["purchase_value"]), 2),
        "transactions": [dict(row) for row in transactions[:10]],
        "budgets": budgets,
        "categories": [dict(row) for row in categories],
        "trend": [dict(row) for row in trend],
    }


def transaction_categories() -> list[str]:
    rows = get_db().execute(
        "SELECT name FROM transaction_categories WHERE user_id = ? ORDER BY name COLLATE NOCASE",
        (g.user["id"],),
    ).fetchall()
    defaults = {"Food & Dining", "Transport", "Subscriptions", "Housing", "Bills & Utilities", "Health", "Income"}
    return sorted(defaults | {row["name"] for row in rows}, key=str.casefold)


def wallet_access(wallet_id: int, minimum: str = "viewer"):
    rank = {"viewer": 1, "editor": 2, "owner": 3}
    return get_db().execute(
        """
        SELECT w.*, m.role, u.full_name AS member_name
        FROM shared_wallets w
        JOIN shared_wallet_members m ON m.wallet_id=w.id AND m.user_id=?
        JOIN users u ON u.id=?
        WHERE w.id=? AND rank(?) <= rank(m.role)
        """.replace("rank(?) <= rank(m.role)", "? <= CASE m.role WHEN 'viewer' THEN 1 WHEN 'editor' THEN 2 WHEN 'owner' THEN 3 END"),
        (g.user["id"], g.user["id"], wallet_id, rank[minimum]),
    ).fetchone()


def wallet_members(wallet_id: int):
    return get_db().execute(
        """
        SELECT m.user_id, m.role, u.full_name, u.email
        FROM shared_wallet_members m JOIN users u ON u.id=m.user_id
        WHERE m.wallet_id=? ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END, u.full_name
        """,
        (wallet_id,),
    ).fetchall()


def wallet_balances(wallet_id: int) -> list[dict]:
    db = get_db()
    members = wallet_members(wallet_id)
    balances = {row["user_id"]: {"user_id": row["user_id"], "name": row["full_name"], "paid": 0.0, "share": 0.0} for row in members}
    expenses = db.execute(
        "SELECT id, paid_by, amount FROM wallet_expenses WHERE wallet_id=?",
        (wallet_id,),
    ).fetchall()
    for expense in expenses:
        if expense["paid_by"] in balances:
            balances[expense["paid_by"]]["paid"] += float(expense["amount"])
        for split in db.execute(
            "SELECT user_id, share_amount FROM wallet_expense_splits WHERE expense_id=?",
            (expense["id"],),
        ):
            if split["user_id"] in balances:
                balances[split["user_id"]]["share"] += float(split["share_amount"])
    for settlement in db.execute(
        "SELECT from_user_id, to_user_id, amount FROM wallet_settlements WHERE wallet_id=?",
        (wallet_id,),
    ):
        amount = float(settlement["amount"])
        if settlement["from_user_id"] in balances:
            balances[settlement["from_user_id"]]["paid"] += amount
        if settlement["to_user_id"] in balances:
            balances[settlement["to_user_id"]]["paid"] -= amount
    return [{**item, "balance": round(item["paid"] - item["share"], 2)} for item in balances.values()]


def wallet_debts(wallet_id: int) -> list[dict]:
    balances = wallet_balances(wallet_id)
    creditors = [[item["user_id"], item["name"], item["balance"]] for item in balances if item["balance"] > 0.005]
    debtors = [[item["user_id"], item["name"], -item["balance"]] for item in balances if item["balance"] < -0.005]
    debts = []
    for debtor in debtors:
        for creditor in creditors:
            amount = min(debtor[2], creditor[2])
            if amount > 0.005:
                debts.append({"from_id": debtor[0], "from_name": debtor[1], "to_id": creditor[0], "to_name": creditor[1], "amount": round(amount, 2)})
                debtor[2] -= amount
                creditor[2] -= amount
    return debts


def validate_transaction_form(form) -> tuple[dict | None, str | None]:
    description = form.get("description", "").strip()
    vendor = form.get("vendor", "").strip()
    category = form.get("category", "").strip()
    notes = form.get("notes", "").strip()
    tags = ",".join(sorted({tag.strip() for tag in form.get("tags", "").split(",") if tag.strip()}))
    transaction_type = form.get("transaction_type", "").strip()
    payment_method = form.get("payment_method", "").strip()
    amount_value = form.get("amount", "").strip()
    transaction_date = form.get("transaction_date", "").strip()
    if not description or len(description) > 160:
        return None, "Description is required and must be 160 characters or fewer."
    if not category or len(category) > 80:
        return None, "Category is required and must be 80 characters or fewer."
    if transaction_type not in TRANSACTION_TYPES:
        return None, "Choose a valid transaction type."
    if payment_method not in PAYMENT_METHODS:
        return None, "Choose a valid payment method."
    try:
        amount = float(amount_value)
    except ValueError:
        return None, "Enter a valid amount."
    if not math.isfinite(amount) or amount <= 0 or amount > 100_000_000:
        return None, "Amount must be greater than zero and within the supported limit."
    try:
        date.fromisoformat(transaction_date)
    except ValueError:
        return None, "Enter a valid transaction date."
    if len(vendor) > 120 or len(notes) > 2000 or len(tags) > 500:
        return None, "Vendor, notes, or tags exceed the allowed length."
    return {
        "title": description,
        "amount": amount,
        "transaction_type": transaction_type,
        "category": category,
        "vendor": vendor,
        "transaction_date": transaction_date,
        "payment_method": payment_method,
        "notes": notes,
        "tags": tags,
    }, None


def save_receipt(upload) -> str | None:
    if not upload or not upload.filename:
        return None
    filename = secure_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not filename or extension not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ValueError("Receipt must be a PDF, PNG, JPG, JPEG, or WEBP file.")
    stored_name = f"{g.user['id']}_{uuid4().hex}.{extension}"
    upload.save(Path(current_app.config["UPLOAD_FOLDER"]) / stored_name)
    return stored_name


def get_owned_transaction(transaction_id: int):
    return get_db().execute(
        "SELECT * FROM transactions WHERE id = ? AND user_id = ?",
        (transaction_id, g.user["id"]),
    ).fetchone()


@main_bp.route("/transactions/new", methods=("GET", "POST"))
@login_required
def new_transaction():
    if request.method == "POST":
        values, error = validate_transaction_form(request.form)
        if error:
            flash(error, "error")
        else:
            try:
                receipt_path = save_receipt(request.files.get("receipt"))
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                db = get_db()
                db.execute(
                    """
                    INSERT INTO transactions
                    (user_id, title, category, amount, transaction_type, transaction_date,
                     vendor, payment_method, notes, tags, receipt_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (g.user["id"], values["title"], values["category"], values["amount"],
                     values["transaction_type"], values["transaction_date"], values["vendor"],
                     values["payment_method"], values["notes"], values["tags"], receipt_path),
                )
                db.execute(
                    "INSERT OR IGNORE INTO transaction_categories (user_id, name) VALUES (?, ?)",
                    (g.user["id"], values["category"]),
                )
                db.commit()
                flash("Transaction added.", "success")
                return redirect(url_for("main.transactions"))
    return render_template("transaction_form.html", transaction=None, categories=transaction_categories(), payment_methods=sorted(PAYMENT_METHODS), page_title="Add transaction")


@main_bp.route("/transactions", methods=("GET", "POST"))
@login_required
def transactions():
    if request.method == "POST":
        values, error = validate_transaction_form(request.form)
        if error:
            flash(error, "error")
        else:
            try:
                receipt_path = save_receipt(request.files.get("receipt"))
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                return _insert_transaction(values, receipt_path)
    query = request.args.get("q", "").strip()
    transaction_type = request.args.get("type", "").strip()
    category = request.args.get("category", "").strip()
    payment_method = request.args.get("payment_method", "").strip()
    sort = request.args.get("sort", "date_desc")
    sort_sql = SORT_FIELDS.get(sort, SORT_FIELDS["date_desc"])
    conditions = ["user_id = ?"]
    params: list = [g.user["id"]]
    if query:
        conditions.append("(title LIKE ? OR vendor LIKE ? OR notes LIKE ? OR tags LIKE ?)")
        search = f"%{query}%"
        params.extend([search] * 4)
    if transaction_type in TRANSACTION_TYPES:
        conditions.append("transaction_type = ?")
        params.append(transaction_type)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if payment_method in PAYMENT_METHODS:
        conditions.append("payment_method = ?")
        params.append(payment_method)
    rows = get_db().execute(
        f"SELECT * FROM transactions WHERE {' AND '.join(conditions)} ORDER BY {sort_sql}",
        params,
    ).fetchall()
    return render_template(
        "transactions.html",
        transactions=rows,
        categories=transaction_categories(),
        payment_methods=sorted(PAYMENT_METHODS),
        filters=request.args,
    )


def _insert_transaction(values: dict, receipt_path: str | None):
    db = get_db()
    db.execute(
        """
        INSERT INTO transactions
        (user_id, title, category, amount, transaction_type, transaction_date,
         vendor, payment_method, notes, tags, receipt_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (g.user["id"], values["title"], values["category"], values["amount"],
         values["transaction_type"], values["transaction_date"], values["vendor"],
         values["payment_method"], values["notes"], values["tags"], receipt_path),
    )
    db.execute(
        "INSERT OR IGNORE INTO transaction_categories (user_id, name) VALUES (?, ?)",
        (g.user["id"], values["category"]),
    )
    db.commit()
    flash("Transaction added.", "success")
    return redirect(url_for("main.transactions"))


@main_bp.route("/transactions/<int:transaction_id>/edit", methods=("GET", "POST"))
@login_required
def edit_transaction(transaction_id: int):
    transaction = get_owned_transaction(transaction_id)
    if transaction is None:
        return "Transaction not found", 404
    if request.method == "POST":
        values, error = validate_transaction_form(request.form)
        if error:
            flash(error, "error")
        else:
            try:
                receipt_path = save_receipt(request.files.get("receipt"))
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                db = get_db()
                db.execute(
                    """
                    UPDATE transactions SET title=?, amount=?, transaction_type=?, category=?,
                    vendor=?, transaction_date=?, payment_method=?, notes=?, tags=?,
                    receipt_path=COALESCE(?, receipt_path)
                    WHERE id=? AND user_id=?
                    """,
                    (values["title"], values["amount"], values["transaction_type"], values["category"],
                     values["vendor"], values["transaction_date"], values["payment_method"], values["notes"],
                     values["tags"], receipt_path, transaction_id, g.user["id"]),
                )
                db.execute(
                    "INSERT OR IGNORE INTO transaction_categories (user_id, name) VALUES (?, ?)",
                    (g.user["id"], values["category"]),
                )
                db.commit()
                flash("Transaction updated.", "success")
                return redirect(url_for("main.transactions"))
    return render_template("transaction_form.html", transaction=transaction, categories=transaction_categories(), payment_methods=sorted(PAYMENT_METHODS), page_title="Edit transaction")


@main_bp.post("/transactions/<int:transaction_id>/delete")
@login_required
def delete_transaction(transaction_id: int):
    transaction = get_owned_transaction(transaction_id)
    if transaction is None:
        return "Transaction not found", 404
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, g.user["id"]))
    db.commit()
    flash("Transaction deleted.", "success")
    return redirect(url_for("main.transactions"))


@main_bp.get("/transactions/<int:transaction_id>/receipt")
@login_required
def transaction_receipt(transaction_id: int):
    transaction = get_owned_transaction(transaction_id)
    if transaction is None or not transaction["receipt_path"]:
        return "Receipt not found", 404
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], transaction["receipt_path"], as_attachment=True)


def receipt_scan(scan_id: int):
    return get_db().execute(
        "SELECT * FROM receipt_scans WHERE id = ? AND user_id = ?",
        (scan_id, g.user["id"]),
    ).fetchone()


def suggested_category(vendor: str, items: list[dict]) -> str:
    text = f"{vendor} {' '.join(item.get('name', '') for item in items)}".lower()
    if any(word in text for word in ("grocery", "market", "food", "restaurant", "cafe")):
        return "Food & Dining"
    if any(word in text for word in ("fuel", "uber", "taxi", "transport")):
        return "Transport"
    if any(word in text for word in ("pharmacy", "medical", "health")):
        return "Health"
    return "General"


@main_bp.route("/receipts/scan", methods=("GET", "POST"))
@login_required
def scan_receipt():
    if request.method == "POST":
        upload = request.files.get("receipt")
        filename = secure_filename(upload.filename if upload else "")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not upload or not filename or extension not in OCR_IMAGE_EXTENSIONS:
            flash("Upload a PNG, JPG, JPEG, or WEBP receipt image.", "error")
        else:
            image_bytes = upload.read()
            if len(image_bytes) > 5 * 1024 * 1024:
                flash("Receipt image must be 5 MB or smaller.", "error")
            else:
                file_hash = hashlib.sha256(image_bytes).hexdigest()
                existing = get_db().execute(
                    "SELECT id FROM receipt_scans WHERE user_id = ? AND file_hash = ? AND status = 'saved'",
                    (g.user["id"], file_hash),
                ).fetchone()
                if existing:
                    flash("This receipt was already saved as a transaction.", "error")
                else:
                    try:
                        extracted = extract_receipt(image_bytes)
                    except (OCRInputError, OCRUnavailableError) as exc:
                        flash(str(exc), "error")
                    else:
                        stored_name = f"{g.user['id']}_{uuid4().hex}.{extension}"
                        Path(current_app.config["OCR_UPLOAD_FOLDER"], stored_name).write_bytes(image_bytes)
                        extracted["suggested_category"] = suggested_category(extracted["vendor"], extracted["items"])
                        db = get_db()
                        try:
                            cursor = db.execute(
                                "INSERT INTO receipt_scans (user_id, file_path, file_hash, extracted_json) VALUES (?, ?, ?, ?)",
                                (g.user["id"], stored_name, file_hash, json.dumps(extracted)),
                            )
                            db.commit()
                        except sqlite3.IntegrityError:
                            db.rollback()
                            flash("This receipt has already been uploaded.", "error")
                        else:
                            return redirect(url_for("main.review_receipt", scan_id=cursor.lastrowid))
    return render_template("receipt_scan.html")


@main_bp.route("/receipts/scan/<int:scan_id>/review", methods=("GET", "POST"))
@login_required
def review_receipt(scan_id: int):
    scan = receipt_scan(scan_id)
    if scan is None or scan["status"] != "review":
        return "Receipt review not found", 404
    extracted = json.loads(scan["extracted_json"])
    if request.method == "POST":
        vendor = request.form.get("vendor", "").strip()
        transaction_date = request.form.get("transaction_date", "").strip()
        category = request.form.get("category", "").strip() or "General"
        invalid_date = False
        try:
            amount = float(request.form.get("total", ""))
            date.fromisoformat(transaction_date)
        except (ValueError, TypeError):
            invalid_date = True
        if invalid_date:
            flash("Correct the vendor, total, and date before saving.", "error")
        elif not vendor or amount <= 0 or len(vendor) > 120 or len(category) > 80:
            flash("Vendor, total, and category are required.", "error")
        else:
            duplicate = get_db().execute(
                """
                SELECT id FROM transactions
                WHERE user_id = ? AND transaction_type = 'expense'
                  AND ABS(amount - ?) < 0.01 AND transaction_date = ?
                  AND LOWER(vendor) = LOWER(?)
                """,
                (g.user["id"], amount, transaction_date, vendor),
            ).fetchone()
            if duplicate:
                flash("A matching transaction already exists for this vendor, date, and total.", "error")
            else:
                try:
                    items = json.loads(request.form.get("items_json", "[]"))
                except json.JSONDecodeError:
                    items = []
                db = get_db()
                db.execute(
                    """
                    INSERT INTO transactions
                    (user_id, title, category, amount, transaction_type, transaction_date,
                     vendor, payment_method, notes, tags, receipt_path)
                    VALUES (?, ?, ?, ?, 'expense', ?, ?, ?, ?, ?, ?)
                    """,
                    (g.user["id"], f"Receipt from {vendor}", category, amount, transaction_date,
                     vendor, request.form.get("payment_method", "card"), request.form.get("notes", "").strip(),
                     "receipt,ocr", scan["file_path"]),
                )
                for item in items:
                    if item.get("selected") == "1" and item.get("name"):
                        try:
                            quantity = max(1, int(item.get("quantity", 1)))
                            price = max(0, float(item.get("price", 0)))
                        except (TypeError, ValueError):
                            continue
                        db.execute(
                            """
                            INSERT INTO goods
                            (user_id, name, category, unit_price, quantity, purchase_date, vendor,
                             notes, estimated_value)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (g.user["id"], item["name"][:160], category, price, quantity,
                             transaction_date, vendor, "Added from scanned receipt", price),
                        )
                db.execute(
                    "INSERT OR IGNORE INTO transaction_categories (user_id, name) VALUES (?, ?)",
                    (g.user["id"], category),
                )
                db.execute("UPDATE receipt_scans SET extracted_json = ?, status = 'saved' WHERE id = ? AND user_id = ?", (json.dumps(extracted), scan_id, g.user["id"]))
                db.commit()
                flash("Receipt saved as a transaction.", "success")
                return redirect(url_for("main.transactions"))
    return render_template("receipt_review.html", scan=scan, extracted=extracted, image_url=url_for("main.receipt_scan_file", scan_id=scan_id))


@main_bp.get("/receipts/scan/<int:scan_id>/file")
@login_required
def receipt_scan_file(scan_id: int):
    scan = receipt_scan(scan_id)
    if scan is None:
        return "Receipt not found", 404
    return send_from_directory(current_app.config["OCR_UPLOAD_FOLDER"], scan["file_path"])


def goods_categories() -> list[str]:
    rows = get_db().execute(
        "SELECT DISTINCT category FROM goods WHERE user_id = ? ORDER BY category COLLATE NOCASE",
        (g.user["id"],),
    ).fetchall()
    return sorted({row["category"] for row in rows} | {"Electronics", "Furniture", "Appliances", "Vehicles", "General"}, key=str.casefold)


def validate_good_form(form):
    name = form.get("name", "").strip()
    category = form.get("category", "").strip()
    vendor = form.get("vendor", "").strip()
    notes = form.get("notes", "").strip()
    purchase_date = form.get("purchase_date", "").strip()
    warranty_expiry = form.get("warranty_expiry", "").strip() or None
    try:
        price = float(form.get("purchase_price", "").strip())
        estimated_value = float(form.get("estimated_value", "").strip())
        quantity = int(form.get("quantity", "").strip())
    except (TypeError, ValueError):
        return None, "Enter valid price, quantity, and estimated value."
    if not name or len(name) > 160:
        return None, "Product name is required and must be 160 characters or fewer."
    if not category or len(category) > 80:
        return None, "Category is required and must be 80 characters or fewer."
    if not math.isfinite(price) or not math.isfinite(estimated_value) or price < 0 or estimated_value < 0 or quantity < 1 or quantity > 100000:
        return None, "Prices cannot be negative and quantity must be at least one."
    try:
        date.fromisoformat(purchase_date)
        if warranty_expiry:
            date.fromisoformat(warranty_expiry)
    except ValueError:
        return None, "Enter valid purchase and warranty dates."
    if len(vendor) > 120 or len(notes) > 2000:
        return None, "Vendor or notes exceed the allowed length."
    return {
        "name": name,
        "category": category,
        "unit_price": price,
        "quantity": quantity,
        "purchase_date": purchase_date,
        "vendor": vendor,
        "warranty_expiry": warranty_expiry,
        "notes": notes,
        "estimated_value": estimated_value,
    }, None


def save_goods_upload(upload, kind: str) -> str | None:
    if not upload or not upload.filename:
        return None
    filename = secure_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = ALLOWED_IMAGE_EXTENSIONS if kind == "photo" else ALLOWED_RECEIPT_EXTENSIONS
    if not filename or extension not in allowed:
        accepted = "PNG, JPG, JPEG, or WEBP images" if kind == "photo" else "PDF or image files"
        raise ValueError(f"Upload must be one of: {accepted}.")
    header = upload.stream.read(16)
    upload.stream.seek(0)
    image_signatures = (
        extension == "png" and header.startswith(b"\x89PNG\r\n\x1a\n"),
        extension in {"jpg", "jpeg"} and header.startswith(b"\xff\xd8\xff"),
        extension == "webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    )
    if kind == "photo" and not any(image_signatures):
        raise ValueError("The product photo does not contain a valid image.")
    if kind == "receipt" and extension == "pdf" and not header.startswith(b"%PDF"):
        raise ValueError("The receipt PDF is not valid.")
    stored_name = f"{g.user['id']}_{uuid4().hex}.{extension}"
    upload.save(Path(current_app.config["GOODS_UPLOAD_FOLDER"]) / stored_name)
    return stored_name


def get_owned_good(good_id: int):
    return get_db().execute(
        "SELECT * FROM goods WHERE id = ? AND user_id = ?", (good_id, g.user["id"])
    ).fetchone()


def save_good(values: dict, photo_path: str | None, receipt_path: str | None, good_id: int | None = None) -> None:
    db = get_db()
    if good_id is None:
        db.execute(
            """
            INSERT INTO goods
            (user_id, name, category, unit_price, quantity, purchase_date, vendor,
             warranty_expiry, receipt_path, photo_path, notes, estimated_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (g.user["id"], values["name"], values["category"], values["unit_price"], values["quantity"],
             values["purchase_date"], values["vendor"], values["warranty_expiry"], receipt_path,
             photo_path, values["notes"], values["estimated_value"]),
        )
    else:
        db.execute(
            """
            UPDATE goods SET name=?, category=?, unit_price=?, quantity=?, purchase_date=?, vendor=?,
            warranty_expiry=?, receipt_path=COALESCE(?, receipt_path),
            photo_path=COALESCE(?, photo_path), notes=?, estimated_value=?
            WHERE id=? AND user_id=?
            """,
            (values["name"], values["category"], values["unit_price"], values["quantity"],
             values["purchase_date"], values["vendor"], values["warranty_expiry"], receipt_path,
             photo_path, values["notes"], values["estimated_value"], good_id, g.user["id"]),
        )
    db.commit()


@main_bp.route("/goods", methods=("GET", "POST"))
@login_required
def goods():
    if request.method == "POST":
        values, error = validate_good_form(request.form)
        if error:
            flash(error, "error")
        else:
            try:
                photo_path = save_goods_upload(request.files.get("photo"), "photo")
                receipt_path = save_goods_upload(request.files.get("receipt"), "receipt")
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                save_good(values, photo_path, receipt_path)
                flash("Good added to your inventory.", "success")
                return redirect(url_for("main.goods"))
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sort = request.args.get("sort", "newest")
    conditions = ["user_id = ?"]
    params: list = [g.user["id"]]
    if query:
        conditions.append("(name LIKE ? OR vendor LIKE ? OR notes LIKE ?)")
        search = f"%{query}%"
        params.extend([search, search, search])
    if category:
        conditions.append("category = ?")
        params.append(category)
    rows = get_db().execute(
        f"SELECT * FROM goods WHERE {' AND '.join(conditions)} ORDER BY {GOODS_SORT_FIELDS.get(sort, GOODS_SORT_FIELDS['newest'])}",
        params,
    ).fetchall()
    totals = get_db().execute(
        "SELECT COALESCE(SUM(quantity), 0) AS quantity, COALESCE(SUM(quantity * unit_price), 0) AS purchase_value, COALESCE(SUM(quantity * estimated_value), 0) AS current_value FROM goods WHERE user_id = ?",
        (g.user["id"],),
    ).fetchone()
    return render_template("goods.html", goods=rows, totals=totals, categories=goods_categories(), filters=request.args)


@main_bp.route("/goods/new", methods=("GET", "POST"))
@login_required
def new_good():
    if request.method == "POST":
        values, error = validate_good_form(request.form)
        if error:
            flash(error, "error")
        else:
            try:
                photo_path = save_goods_upload(request.files.get("photo"), "photo")
                receipt_path = save_goods_upload(request.files.get("receipt"), "receipt")
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                save_good(values, photo_path, receipt_path)
                flash("Good added to your inventory.", "success")
                return redirect(url_for("main.goods"))
    return render_template("good_form.html", good=None, categories=goods_categories(), page_title="Add good")


@main_bp.route("/goods/<int:good_id>", methods=("GET",))
@login_required
def good_detail(good_id: int):
    good = get_owned_good(good_id)
    if good is None:
        return "Good not found", 404
    return render_template("good_detail.html", good=good)


@main_bp.route("/goods/<int:good_id>/edit", methods=("GET", "POST"))
@login_required
def edit_good(good_id: int):
    good = get_owned_good(good_id)
    if good is None:
        return "Good not found", 404
    if request.method == "POST":
        values, error = validate_good_form(request.form)
        if error:
            flash(error, "error")
        else:
            try:
                photo_path = save_goods_upload(request.files.get("photo"), "photo")
                receipt_path = save_goods_upload(request.files.get("receipt"), "receipt")
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                save_good(values, photo_path, receipt_path, good_id)
                flash("Inventory item updated.", "success")
                return redirect(url_for("main.good_detail", good_id=good_id))
    return render_template("good_form.html", good=good, categories=goods_categories(), page_title="Edit good")


@main_bp.post("/goods/<int:good_id>/delete")
@login_required
def delete_good(good_id: int):
    if get_owned_good(good_id) is None:
        return "Good not found", 404
    db = get_db()
    db.execute("DELETE FROM goods WHERE id = ? AND user_id = ?", (good_id, g.user["id"]))
    db.commit()
    flash("Inventory item deleted.", "success")
    return redirect(url_for("main.goods"))


@main_bp.get("/goods/<int:good_id>/file/<string:kind>")
@login_required
def good_file(good_id: int, kind: str):
    good = get_owned_good(good_id)
    if good is None or kind not in {"photo", "receipt"}:
        return "File not found", 404
    path = good["photo_path"] if kind == "photo" else good["receipt_path"]
    if not path:
        return "File not found", 404
    return send_from_directory(current_app.config["GOODS_UPLOAD_FOLDER"], path, as_attachment=kind == "receipt")


def budget_window(period: str, anchor: date | None = None) -> tuple[date, date]:
    anchor = anchor or date.today()
    if period == "weekly":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if period == "yearly":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    return date(anchor.year, anchor.month, 1), date(anchor.year + (anchor.month == 12), (anchor.month % 12) + 1, 1) - timedelta(days=1)


def previous_budget_window(period: str, anchor: date | None = None) -> tuple[date, date]:
    start, _ = budget_window(period, anchor)
    if period == "weekly":
        return start - timedelta(days=7), start - timedelta(days=1)
    if period == "yearly":
        return date(start.year - 1, 1, 1), date(start.year - 1, 12, 31)
    previous_month = start - timedelta(days=1)
    return budget_window("monthly", previous_month)


def actual_expenses(start: date, end: date, category: str | None = None) -> float:
    conditions = ["user_id = ?", "transaction_type = 'expense'", "transaction_date BETWEEN ? AND ?"]
    params: list = [g.user["id"], start.isoformat(), end.isoformat()]
    if category:
        conditions.append("category = ?")
        params.append(category)
    row = get_db().execute(
        f"SELECT COALESCE(SUM(amount), 0) AS spent FROM transactions WHERE {' AND '.join(conditions)}",
        params,
    ).fetchone()
    return round(float(row["spent"]), 2)


def budgeting_data() -> dict:
    today = date.today()
    db = get_db()
    budgets = db.execute(
        "SELECT * FROM budgets WHERE user_id = ? ORDER BY period, category COLLATE NOCASE",
        (g.user["id"],),
    ).fetchall()
    budget_rows = []
    for budget in budgets:
        start, end = budget_window(budget["period"], today)
        spent = actual_expenses(start, end, budget["category"])
        rollover = 0.0
        if budget["rollover_enabled"]:
            previous_start, previous_end = previous_budget_window(budget["period"], today)
            rollover = max(float(budget["limit_amount"]) - actual_expenses(previous_start, previous_end, budget["category"]), 0)
        limit = round(float(budget["limit_amount"]) + float(budget["rollover_amount"]) + rollover, 2)
        utilization = round((spent / limit) * 100, 1) if limit else 0
        budget_rows.append({
            **dict(budget),
            "spent": spent,
            "effective_limit": limit,
            "remaining": round(limit - spent, 2),
            "utilization": utilization,
            "alert": "exceeded" if utilization >= 100 else "critical" if utilization >= 80 else "warning" if utilization >= 50 else "healthy",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        })
    month_start, month_end = budget_window("monthly", today)
    income_row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS income FROM transactions WHERE user_id = ? AND transaction_type = 'income' AND transaction_date BETWEEN ? AND ?",
        (g.user["id"], month_start.isoformat(), month_end.isoformat()),
    ).fetchone()
    income = float(income_row["income"])
    needs_categories = {"Housing", "Bills", "Bills & Utilities", "Health", "Transport", "Groceries"}
    category_spend = db.execute(
        "SELECT category, COALESCE(SUM(amount), 0) AS amount FROM transactions WHERE user_id = ? AND transaction_type = 'expense' AND transaction_date BETWEEN ? AND ? GROUP BY category",
        (g.user["id"], month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    needs = sum(float(row["amount"]) for row in category_spend if row["category"] in needs_categories)
    wants = sum(float(row["amount"]) for row in category_spend if row["category"] not in needs_categories)
    goals = db.execute(
        "SELECT * FROM savings_goals WHERE user_id = ? ORDER BY goal_type DESC, target_date IS NULL, target_date",
        (g.user["id"],),
    ).fetchall()
    bills = db.execute(
        "SELECT * FROM recurring_expenses WHERE user_id = ? AND active = 1 AND next_due_date BETWEEN ? AND ? ORDER BY next_due_date",
        (g.user["id"], today.isoformat(), (today + timedelta(days=30)).isoformat()),
    ).fetchall()
    return {
        "budgets": budget_rows,
        "income": round(income, 2),
        "needs": round(needs, 2),
        "wants": round(wants, 2),
        "savings": round(max(income - needs - wants, 0), 2),
        "goals": [dict(goal) | {"progress": round(min(float(goal["current_amount"]) / float(goal["target_amount"]) * 100, 100), 1)} for goal in goals],
        "bills": [dict(bill) for bill in bills],
    }


def validate_budget_form(form):
    category = form.get("category", "").strip() or None
    period = form.get("period", "").strip()
    try:
        limit_amount = float(form.get("limit_amount", ""))
        rollover_amount = float(form.get("rollover_amount", "0") or 0)
    except ValueError:
        return None, "Enter valid budget amounts."
    if not math.isfinite(limit_amount) or not math.isfinite(rollover_amount) or period not in BUDGET_PERIODS or limit_amount < 0 or rollover_amount < 0:
        return None, "Choose a valid period and non-negative amounts."
    if category and len(category) > 80:
        return None, "Category must be 80 characters or fewer."
    return {"category": category, "period": period, "limit_amount": limit_amount, "rollover_amount": rollover_amount, "rollover_enabled": 1 if form.get("rollover_enabled") else 0}, None


@main_bp.route("/budgets", methods=("GET", "POST"))
@login_required
def budgets():
    if request.method == "POST":
        values, error = validate_budget_form(request.form)
        if error:
            flash(error, "error")
        else:
            db = get_db()
            existing = db.execute(
                "SELECT id FROM budgets WHERE user_id = ? AND category IS ? AND period = ?",
                (g.user["id"], values["category"], values["period"]),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE budgets SET limit_amount=?, rollover_enabled=?, rollover_amount=? WHERE id=? AND user_id=?",
                    (values["limit_amount"], values["rollover_enabled"], values["rollover_amount"], existing["id"], g.user["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO budgets (user_id, category, period, limit_amount, rollover_enabled, rollover_amount) VALUES (?, ?, ?, ?, ?, ?)",
                    (g.user["id"], values["category"], values["period"], values["limit_amount"], values["rollover_enabled"], values["rollover_amount"]),
                )
            db.commit()
            flash("Budget saved.", "success")
            return redirect(url_for("main.budgets"))
    return render_template("budgets.html", data=budgeting_data(), categories=transaction_categories())


@main_bp.post("/budgets/<int:budget_id>/delete")
@login_required
def delete_budget(budget_id: int):
    db = get_db()
    if not db.execute("SELECT id FROM budgets WHERE id=? AND user_id=?", (budget_id, g.user["id"])).fetchone():
        return "Budget not found", 404
    db.execute("DELETE FROM budgets WHERE id=? AND user_id=?", (budget_id, g.user["id"]))
    db.commit()
    flash("Budget deleted.", "success")
    return redirect(url_for("main.budgets"))


def validate_goal_form(form):
    name = form.get("name", "").strip()
    goal_type = form.get("goal_type", "").strip()
    try:
        target = float(form.get("target_amount", ""))
        current = float(form.get("current_amount", "0") or 0)
        contribution = float(form.get("monthly_contribution", "0") or 0)
    except ValueError:
        return None, "Enter valid savings amounts."
    target_date = form.get("target_date", "").strip() or None
    if not all(math.isfinite(value) for value in (target, current, contribution)) or not name or len(name) > 120 or goal_type not in GOAL_TYPES or target <= 0 or current < 0 or contribution < 0 or current > target:
        return None, "Enter a name, valid goal type, and non-negative amounts."
    try:
        if target_date:
            date.fromisoformat(target_date)
    except ValueError:
        return None, "Enter a valid target date."
    return {"name": name, "goal_type": goal_type, "target_amount": target, "current_amount": current, "monthly_contribution": contribution, "target_date": target_date}, None


@main_bp.route("/savings", methods=("GET", "POST"))
@login_required
def savings():
    if request.method == "POST":
        values, error = validate_goal_form(request.form)
        if error:
            flash(error, "error")
        else:
            db = get_db()
            db.execute(
                "INSERT INTO savings_goals (user_id, name, target_amount, current_amount, target_date, goal_type, monthly_contribution) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (g.user["id"], values["name"], values["target_amount"], values["current_amount"], values["target_date"], values["goal_type"], values["monthly_contribution"]),
            )
            db.commit()
            flash("Savings goal saved.", "success")
            return redirect(url_for("main.savings"))
    return render_template("savings.html", data=budgeting_data())


@main_bp.post("/savings/<int:goal_id>/delete")
@login_required
def delete_savings_goal(goal_id: int):
    db = get_db()
    if not db.execute("SELECT id FROM savings_goals WHERE id=? AND user_id=?", (goal_id, g.user["id"])).fetchone():
        return "Savings goal not found", 404
    db.execute("DELETE FROM savings_goals WHERE id=? AND user_id=?", (goal_id, g.user["id"]))
    db.commit()
    flash("Savings goal deleted.", "success")
    return redirect(url_for("main.savings"))


@main_bp.post("/bills")
@login_required
def add_bill():
    title = request.form.get("title", "").strip()
    frequency = request.form.get("frequency", "").strip()
    due_date = request.form.get("next_due_date", "").strip()
    try:
        amount = float(request.form.get("amount", ""))
        date.fromisoformat(due_date)
    except (ValueError, TypeError):
        flash("Enter a valid bill amount and due date.", "error")
        return redirect(url_for("main.budgets"))
    if not title or amount < 0 or frequency not in {"weekly", "monthly", "yearly"}:
        flash("Enter a valid bill title, frequency, and amount.", "error")
        return redirect(url_for("main.budgets"))
    db = get_db()
    db.execute(
        "INSERT INTO recurring_expenses (user_id, title, amount, frequency, next_due_date, category, payment_method) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], title, amount, frequency, due_date, request.form.get("category", "Bills"), request.form.get("payment_method", "bank")),
    )
    db.commit()
    flash("Bill reminder added.", "success")
    return redirect(url_for("main.budgets"))


@main_bp.get("/")
@login_required
def dashboard():
    period = request.args.get("period", "monthly")
    start, end = date_window(period, request.args.get("start"), request.args.get("end"))
    return render_template(
        "dashboard.html",
        dashboard=dashboard_data(period, start, end),
        period=period,
        start=start,
        end=end,
    )


@main_bp.get("/accounts")
@login_required
def accounts():
    return render_template("management_placeholder.html", page_title="Accounts", eyebrow="Connected accounts", message="External bank connections are not configured. Your manually entered records remain private and available in HisabSmart.")


@main_bp.get("/settings")
@login_required
def settings():
    return render_template("management_placeholder.html", page_title="Settings", eyebrow="Your workspace", message="Your account is protected by a hashed password, server-side sessions, and CSRF-protected forms. Change the deployment secret before using HisabSmart in production.")


@main_bp.route("/wallets", methods=("GET", "POST"))
@login_required
def wallets():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name or len(name) > 120:
            flash("Enter a wallet name of 120 characters or fewer.", "error")
        else:
            cursor = db.execute("INSERT INTO shared_wallets (owner_id, name) VALUES (?, ?)", (g.user["id"], name))
            db.execute(
                "INSERT INTO shared_wallet_members (wallet_id, user_id, role) VALUES (?, ?, 'owner')",
                (cursor.lastrowid, g.user["id"]),
            )
            db.commit()
            flash("Shared wallet created.", "success")
            return redirect(url_for("main.wallet_detail", wallet_id=cursor.lastrowid))
    rows = db.execute(
        """
        SELECT w.id, w.name, w.owner_id, m.role, COUNT(members.user_id) member_count
        FROM shared_wallets w
        JOIN shared_wallet_members m ON m.wallet_id=w.id AND m.user_id=?
        JOIN shared_wallet_members members ON members.wallet_id=w.id
        GROUP BY w.id ORDER BY w.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("wallets.html", wallets=rows)


@main_bp.route("/wallets/<int:wallet_id>", methods=("GET", "POST"))
@login_required
def wallet_detail(wallet_id: int):
    access = wallet_access(wallet_id)
    if access is None:
        return "Wallet not found", 404
    db = get_db()
    if request.method == "POST":
        if wallet_access(wallet_id, "editor") is None:
            return "Editor permission required", 403
        title = request.form.get("title", "").strip()
        expense_date = request.form.get("expense_date", "").strip()
        split_method = request.form.get("split_method", "").strip()
        try:
            amount = float(request.form.get("amount", ""))
            date.fromisoformat(expense_date)
        except (ValueError, TypeError):
            flash("Enter a valid amount and date.", "error")
        else:
            member_ids = [int(value) for value in request.form.getlist("member_ids") if value.isdigit()]
            members = wallet_members(wallet_id)
            allowed_ids = {row["user_id"] for row in members}
            if not math.isfinite(amount) or not title or len(title) > 160 or amount <= 0 or split_method not in SPLIT_METHODS:
                flash("Enter a valid title, amount, and split method.", "error")
            elif not member_ids or len(member_ids) != len(set(member_ids)) or not set(member_ids).issubset(allowed_ids):
                flash("Choose valid wallet members for the split.", "error")
            else:
                proportions = {}
                if split_method == "proportional":
                    for member_id in member_ids:
                        try:
                            proportions[member_id] = float(request.form.get(f"proportion_{member_id}", ""))
                        except ValueError:
                            proportions[member_id] = -1
                    if any(not math.isfinite(value) or value <= 0 for value in proportions.values()) or abs(sum(proportions.values()) - 100) > 0.01:
                        flash("Proportional shares must be positive and total 100%.", "error")
                        proportions = None
                else:
                    proportions = {member_id: 100 / len(member_ids) for member_id in member_ids}
                if proportions is not None:
                    cursor = db.execute(
                        """
                        INSERT INTO wallet_expenses
                        (wallet_id, paid_by, title, amount, expense_date, split_method)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (wallet_id, g.user["id"], title, amount, expense_date, split_method),
                    )
                    for member_id in member_ids:
                        share = round(amount * proportions[member_id] / 100, 2)
                        db.execute(
                            "INSERT INTO wallet_expense_splits (expense_id, user_id, share_amount, proportion) VALUES (?, ?, ?, ?)",
                            (cursor.lastrowid, member_id, share, proportions[member_id]),
                        )
                    db.commit()
                    flash("Shared expense added.", "success")
                    return redirect(url_for("main.wallet_detail", wallet_id=wallet_id))
    expenses = db.execute(
        """
        SELECT e.*, u.full_name AS paid_by_name
        FROM wallet_expenses e JOIN users u ON u.id=e.paid_by
        WHERE e.wallet_id=? ORDER BY e.expense_date DESC, e.id DESC
        """,
        (wallet_id,),
    ).fetchall()
    return render_template(
        "wallet_detail.html", wallet=access, members=wallet_members(wallet_id),
        expenses=expenses, balances=wallet_balances(wallet_id), debts=wallet_debts(wallet_id),
    )


@main_bp.post("/wallets/<int:wallet_id>/invite")
@login_required
def invite_wallet_member(wallet_id: int):
    if wallet_access(wallet_id, "owner") is None:
        return "Owner permission required", 403
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "editor").strip()
    if role not in WALLET_ROLES:
        return "Invalid wallet role", 400
    user = get_db().execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if user is None:
        flash("That email must belong to an existing HisabSmart user.", "error")
    else:
        try:
            get_db().execute(
                "INSERT INTO shared_wallet_members (wallet_id, user_id, role) VALUES (?, ?, ?)",
                (wallet_id, user["id"], role),
            )
            get_db().commit()
            flash("Member added to the wallet.", "success")
        except sqlite3.IntegrityError:
            flash("That user is already a wallet member.", "error")
    return redirect(url_for("main.wallet_detail", wallet_id=wallet_id))


@main_bp.post("/wallets/<int:wallet_id>/members/<int:user_id>/role")
@login_required
def update_wallet_role(wallet_id: int, user_id: int):
    if wallet_access(wallet_id, "owner") is None:
        return "Owner permission required", 403
    role = request.form.get("role", "")
    if role not in WALLET_ROLES:
        return "Invalid wallet role", 400
    if user_id == g.user["id"]:
        return "The wallet owner role cannot be changed", 400
    db = get_db()
    db.execute("UPDATE shared_wallet_members SET role=? WHERE wallet_id=? AND user_id=?", (role, wallet_id, user_id))
    db.commit()
    return redirect(url_for("main.wallet_detail", wallet_id=wallet_id))


@main_bp.post("/wallets/<int:wallet_id>/settle")
@login_required
def settle_wallet(wallet_id: int):
    if wallet_access(wallet_id, "editor") is None:
        return "Editor permission required", 403
    try:
        from_user_id = int(request.form.get("from_user_id", ""))
        to_user_id = int(request.form.get("to_user_id", ""))
        amount = float(request.form.get("amount", ""))
        settled_at = date.fromisoformat(request.form.get("settled_at", ""))
    except (ValueError, TypeError):
        flash("Enter valid settlement details.", "error")
        return redirect(url_for("main.wallet_detail", wallet_id=wallet_id))
    member_ids = {row["user_id"] for row in wallet_members(wallet_id)}
    if not math.isfinite(amount) or from_user_id == to_user_id or amount <= 0 or {from_user_id, to_user_id} - member_ids:
        return "Invalid settlement participants", 400
    db = get_db()
    db.execute(
        "INSERT INTO wallet_settlements (wallet_id, from_user_id, to_user_id, amount, settled_at, note) VALUES (?, ?, ?, ?, ?, ?)",
        (wallet_id, from_user_id, to_user_id, amount, settled_at.isoformat(), request.form.get("note", "").strip()[:500]),
    )
    db.commit()
    flash("Settlement recorded. No money was transferred.", "success")
    return redirect(url_for("main.wallet_detail", wallet_id=wallet_id))


@main_bp.route("/assistant", methods=("GET",))
@login_required
def assistant():
    return render_template("assistant.html", initial_insights=build_insights(g.user["id"]))


@main_bp.post("/api/assistant/ask")
@login_required
def ask_assistant():
    payload = request.get_json(silent=True) or request.form
    question = payload.get("question", "")
    try:
        result = answer_question(g.user["id"], question)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        current_app.logger.exception("Financial assistant request failed")
        return jsonify({
            "error": "The assistant is temporarily unavailable. Your financial records were not changed."
        }), 503
    return jsonify(result)


def report_window(period: str, start_value: str | None, end_value: str | None) -> tuple[date, date]:
    today = date.today()
    if period == "custom":
        try:
            start = date.fromisoformat(start_value or "")
            end = date.fromisoformat(end_value or "")
        except ValueError:
            start, end = today - timedelta(days=29), today
        return (end, start) if start > end else (start, end)
    if period == "weekly":
        start = today - timedelta(days=6)
    elif period == "yearly":
        start = today - timedelta(days=364)
    else:
        start = today - timedelta(days=29)
    return start, today


def report_data(period: str, start: date, end: date) -> dict:
    db = get_db()
    user_id = g.user["id"]
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    try:
        year_start, year_end = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
    except ValueError:
        year_start, year_end = start - timedelta(days=365), end - timedelta(days=365)

    def totals(begin: date, finish: date) -> tuple[float, float]:
        row = db.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END), 0) income,
                   COALESCE(SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END), 0) expenses
            FROM transactions WHERE user_id=? AND transaction_date BETWEEN ? AND ?
            """,
            (user_id, begin.isoformat(), finish.isoformat()),
        ).fetchone()
        return float(row["income"]), float(row["expenses"])

    income, expenses = totals(start, end)
    prev_income, prev_expenses = totals(previous_start, previous_end)
    yoy_income, yoy_expenses = totals(year_start, year_end)
    category_rows = db.execute(
        """
        SELECT category, ROUND(SUM(amount), 2) total FROM transactions
        WHERE user_id=? AND transaction_type='expense' AND transaction_date BETWEEN ? AND ?
        GROUP BY category ORDER BY total DESC
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    vendor_rows = db.execute(
        """
        SELECT COALESCE(NULLIF(vendor, ''), 'Unknown vendor') vendor,
               ROUND(SUM(amount), 2) total, COUNT(*) purchases
        FROM transactions
        WHERE user_id=? AND transaction_type='expense' AND transaction_date BETWEEN ? AND ?
        GROUP BY vendor ORDER BY total DESC LIMIT 10
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    largest = db.execute(
        """
        SELECT title, vendor, category, amount, transaction_date
        FROM transactions WHERE user_id=? AND transaction_type='expense'
        AND transaction_date BETWEEN ? AND ? ORDER BY amount DESC, id DESC LIMIT 10
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    trend = db.execute(
        """
        SELECT substr(transaction_date, 1, 7) bucket,
               ROUND(SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END), 2) income,
               ROUND(SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END), 2) expenses
        FROM transactions WHERE user_id=? AND transaction_date BETWEEN ? AND ?
        GROUP BY bucket ORDER BY bucket
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    savings_rate = round((income - expenses) / income * 100, 1) if income else 0
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "income": round(income, 2), "expenses": round(expenses, 2),
        "cash_flow": round(income - expenses, 2), "savings_rate": savings_rate,
        "categories": [dict(row) for row in category_rows],
        "vendors": [dict(row) for row in vendor_rows],
        "largest": [dict(row) for row in largest],
        "trend": [dict(row) for row in trend],
        "comparison": {
            "previous_income": round(prev_income, 2), "previous_expenses": round(prev_expenses, 2),
            "yoy_income": round(yoy_income, 2), "yoy_expenses": round(yoy_expenses, 2),
        },
    }


@main_bp.get("/reports")
@login_required
def reports():
    period = request.args.get("period", "monthly")
    start, end = report_window(period, request.args.get("start"), request.args.get("end"))
    return render_template("reports.html", report=report_data(period, start, end), period=period)


@main_bp.get("/reports/export/<string:export_format>")
@login_required
def export_report(export_format: str):
    if export_format not in {"csv", "xlsx", "pdf"}:
        return "Unsupported export format", 400
    start, end = report_window(request.args.get("period", "monthly"), request.args.get("start"), request.args.get("end"))
    data = report_data(request.args.get("period", "monthly"), start, end)
    rows = [["Date", "Description", "Vendor", "Category", "Type", "Amount"]]
    for row in get_db().execute(
        """
        SELECT transaction_date, title, vendor, category, transaction_type, amount
        FROM transactions WHERE user_id=? AND transaction_date BETWEEN ? AND ?
        ORDER BY transaction_date, id
        """,
        (g.user["id"], start.isoformat(), end.isoformat()),
    ):
        rows.append([row["transaction_date"], row["title"], row["vendor"], row["category"], row["transaction_type"], row["amount"]])
    filename = f"hisabsmart-report-{start.isoformat()}-to-{end.isoformat()}"
    if export_format == "csv":
        output = StringIO()
        csv.writer(output).writerows(rows)
        return current_app.response_class(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}.csv"})
    if export_format == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Transactions"
        for row in rows:
            sheet.append(row)
        sheet.append([])
        sheet.append(["Income", data["income"]])
        sheet.append(["Expenses", data["expenses"]])
        sheet.append(["Cash flow", data["cash_flow"]])
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return current_app.response_class(stream.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"})
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph

    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=letter)
    styles = getSampleStyleSheet()
    content = [Paragraph("HisabSmart financial report", styles["Title"]), Paragraph(f"{start} to {end}", styles["Normal"]), Spacer(1, 12)]
    content.append(Paragraph(f"Income: Rs. {data['income']:.2f} - Expenses: Rs. {data['expenses']:.2f} - Cash flow: Rs. {data['cash_flow']:.2f}", styles["Normal"]))
    table = Table(rows[:1] + rows[1:101], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FF5A1F")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    content.extend([Spacer(1, 12), table])
    document.build(content)
    stream.seek(0)
    return current_app.response_class(stream.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}.pdf"})


RECURRING_FREQUENCIES = {"weekly", "monthly", "quarterly", "yearly"}


def owned_recurring(payment_id: int):
    return get_db().execute(
        "SELECT * FROM recurring_expenses WHERE id=? AND user_id=?",
        (payment_id, g.user["id"]),
    ).fetchone()


def validate_recurring_form(form):
    title = form.get("title", "").strip()
    frequency = form.get("frequency", "").strip()
    due_date = form.get("next_due_date", "").strip()
    category = form.get("category", "").strip() or "Subscriptions"
    vendor = form.get("vendor", "").strip()
    notes = form.get("notes", "").strip()
    try:
        amount = float(form.get("amount", ""))
        date.fromisoformat(due_date)
    except (ValueError, TypeError):
        return None, "Enter a valid amount and next payment date."
    if not math.isfinite(amount) or not title or len(title) > 160 or frequency not in RECURRING_FREQUENCIES or amount <= 0:
        return None, "Enter a title, positive amount, and valid frequency."
    if len(category) > 80 or len(vendor) > 120 or len(notes) > 2000:
        return None, "Category, vendor, or notes are too long."
    return {
        "title": title, "amount": amount, "frequency": frequency,
        "next_due_date": due_date, "category": category, "vendor": vendor,
        "payment_method": form.get("payment_method", "bank"),
        "notes": notes,
    }, None


@main_bp.route("/recurring", methods=("GET", "POST"))
@login_required
def recurring():
    if request.method == "POST":
        values, error = validate_recurring_form(request.form)
        if error:
            flash(error, "error")
        else:
            db = get_db()
            duplicate = db.execute(
                """
                SELECT id FROM recurring_expenses
                WHERE user_id=? AND LOWER(title)=LOWER(?) AND amount=? AND status != 'cancelled'
                """,
                (g.user["id"], values["title"], values["amount"]),
            ).fetchone()
            if duplicate:
                flash("A recurring payment with the same title and amount already exists.", "error")
            else:
                db.execute(
                    """
                    INSERT INTO recurring_expenses
                    (user_id, title, amount, frequency, next_due_date, category, payment_method, vendor, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (g.user["id"], values["title"], values["amount"], values["frequency"], values["next_due_date"],
                     values["category"], values["payment_method"], values["vendor"], values["notes"]),
                )
                db.commit()
                flash("Recurring payment created.", "success")
                return redirect(url_for("main.recurring"))
    payment_rows = get_db().execute(
        """
        SELECT r.*, COALESCE(MAX(t.transaction_date), '') last_paid_date,
               COALESCE(MAX(t.amount), 0) last_paid_amount
        FROM recurring_expenses r
        LEFT JOIN transactions t ON t.user_id=r.user_id AND t.tags LIKE '%recurring%'
          AND t.title=r.title
        WHERE r.user_id=? GROUP BY r.id ORDER BY r.next_due_date
        """,
        (g.user["id"],),
    ).fetchall()
    payments = []
    for row in payment_rows:
        item = dict(row)
        history = json.loads(item.get("price_history") or "[]")
        item["price_increased"] = bool(history)
        payments.append(item)
    history_rows = get_db().execute(
        """
        SELECT r.title, run.due_date, t.amount, t.transaction_date
        FROM recurring_payment_runs run
        JOIN recurring_expenses r ON r.id=run.recurring_id
        LEFT JOIN transactions t ON t.id=run.transaction_id
        WHERE run.user_id=? ORDER BY run.due_date DESC LIMIT 30
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("recurring.html", payments=payments, history=history_rows, page_title="Recurring payments")


@main_bp.route("/recurring/<int:payment_id>/edit", methods=("GET", "POST"))
@login_required
def edit_recurring(payment_id: int):
    payment = owned_recurring(payment_id)
    if payment is None:
        return "Recurring payment not found", 404
    if request.method == "POST":
        values, error = validate_recurring_form(request.form)
        if error:
            flash(error, "error")
        else:
            history = json.loads(payment["price_history"] or "[]")
            if values["amount"] > payment["amount"]:
                history.append({"from": payment["amount"], "to": values["amount"], "date": date.today().isoformat()})
            db = get_db()
            db.execute(
                """
                UPDATE recurring_expenses SET title=?, amount=?, frequency=?, next_due_date=?,
                category=?, payment_method=?, vendor=?, notes=?, price_history=?
                WHERE id=? AND user_id=?
                """,
                (values["title"], values["amount"], values["frequency"], values["next_due_date"],
                 values["category"], values["payment_method"], values["vendor"], values["notes"],
                 json.dumps(history), payment_id, g.user["id"]),
            )
            db.commit()
            flash("Recurring payment updated.", "success")
            return redirect(url_for("main.recurring"))
    return render_template("recurring_form.html", payment=payment, page_title="Edit recurring payment")


@main_bp.post("/recurring/<int:payment_id>/<string:action>")
@login_required
def recurring_action(payment_id: int, action: str):
    if action not in {"pause", "resume", "cancel"}:
        return "Unsupported action", 400
    payment = owned_recurring(payment_id)
    if payment is None:
        return "Recurring payment not found", 404
    status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
    get_db().execute(
        "UPDATE recurring_expenses SET status=?, active=? WHERE id=? AND user_id=?",
        (status, 1 if status == "active" else 0, payment_id, g.user["id"]),
    )
    get_db().commit()
    flash(f"Recurring payment {status}.", "success")
    return redirect(url_for("main.recurring"))


@main_bp.get("/api/overview")
@login_required
def overview():
    period = request.args.get("period", "monthly")
    start, end = date_window(period, request.args.get("start"), request.args.get("end"))
    return jsonify(dashboard_data(period, start, end))
