from __future__ import annotations

from datetime import date, timedelta
import math
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from auth import login_required
from db import get_db


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
GOODS_SORT_FIELDS = {
    "newest": "purchase_date DESC, id DESC",
    "oldest": "purchase_date ASC, id ASC",
    "name": "name COLLATE NOCASE ASC, id ASC",
    "purchase_high": "unit_price * quantity DESC, id DESC",
    "value_high": "estimated_value * quantity DESC, id DESC",
}


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
    budgets = db.execute(
        "SELECT category, spent, limit_amount FROM budgets WHERE user_id = ? ORDER BY spent DESC",
        (user_id,),
    ).fetchall()
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
    budget_total = sum(row["limit_amount"] for row in budgets)
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
        "budgets": [dict(row) for row in budgets],
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
    if price < 0 or estimated_value < 0 or quantity < 1 or quantity > 100000:
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


@main_bp.get("/api/overview")
@login_required
def overview():
    period = request.args.get("period", "monthly")
    start, end = date_window(period, request.args.get("start"), request.args.get("end"))
    return jsonify(dashboard_data(period, start, end))
