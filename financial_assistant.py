from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
import math
import re
from typing import Any

from db import get_db


AMOUNT_PATTERN = re.compile(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def _money(value: float) -> str:
    return f"Rs. {value:,.2f}"


def _month_window(today: date) -> tuple[str, str]:
    start = today.replace(day=1)
    end = today.replace(day=monthrange(today.year, today.month)[1])
    return start.isoformat(), end.isoformat()


def _month_summary(user_id: int, today: date) -> dict[str, float]:
    start, end = _month_window(today)
    row = get_db().execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN transaction_type='income' THEN amount ELSE 0 END), 0) income,
          COALESCE(SUM(CASE WHEN transaction_type='expense' THEN amount ELSE 0 END), 0) expenses
        FROM transactions
        WHERE user_id=? AND transaction_date BETWEEN ? AND ?
        """,
        (user_id, start, min(end, today.isoformat())),
    ).fetchone()
    income = float(row["income"])
    expenses = float(row["expenses"])
    return {"income": income, "expenses": expenses, "cash_flow": income - expenses}


def _budget_snapshot(user_id: int, today: date) -> dict[str, Any]:
    start, end = _month_window(today)
    rows = get_db().execute(
        """
        SELECT b.category, b.limit_amount,
               COALESCE(SUM(CASE WHEN t.transaction_type='expense' THEN t.amount ELSE 0 END), 0) spent
        FROM budgets b
        LEFT JOIN transactions t ON t.user_id=b.user_id
          AND t.transaction_date BETWEEN ? AND ?
          AND (b.category IS NULL OR b.category='' OR t.category=b.category)
        WHERE b.user_id=? AND b.period='monthly'
        GROUP BY b.id
        """,
        (start, min(end, today.isoformat()), user_id),
    ).fetchall()
    return {
        "budgets": [dict(row) for row in rows],
        "remaining": sum(max(float(row["limit_amount"]) - float(row["spent"]), 0) for row in rows),
    }


def _duplicate_alerts(user_id: int, today: date) -> list[dict[str, Any]]:
    since = (today - timedelta(days=90)).isoformat()
    rows = get_db().execute(
        """
        SELECT title, amount, transaction_date, COALESCE(vendor, '') vendor, COUNT(*) count
        FROM transactions
        WHERE user_id=? AND transaction_type='expense' AND transaction_date >= ?
        GROUP BY title, amount, transaction_date, vendor
        HAVING COUNT(*) > 1
        ORDER BY transaction_date DESC
        LIMIT 10
        """,
        (user_id, since),
    ).fetchall()
    return [dict(row) for row in rows]


def _anomalies(user_id: int, today: date) -> list[dict[str, Any]]:
    since = (today - timedelta(days=90)).isoformat()
    rows = get_db().execute(
        """
        SELECT title, amount, category, transaction_date
        FROM transactions
        WHERE user_id=? AND transaction_type='expense' AND transaction_date >= ?
        ORDER BY transaction_date DESC
        """,
        (user_id, since),
    ).fetchall()
    amounts = [float(row["amount"]) for row in rows]
    if len(amounts) < 4:
        return []
    average = sum(amounts) / len(amounts)
    deviation = math.sqrt(sum((amount - average) ** 2 for amount in amounts) / len(amounts))
    threshold = average + max(deviation * 2, average)
    return [
        {**dict(row), "threshold": round(threshold, 2)}
        for row in rows
        if float(row["amount"]) >= threshold
    ][:10]


def _month_end_prediction(user_id: int, today: date) -> dict[str, float]:
    summary = _month_summary(user_id, today)
    elapsed = today.day
    days_in_month = monthrange(today.year, today.month)[1]
    remaining_days = max(days_in_month - elapsed, 0)
    projected_income = summary["income"] / elapsed * days_in_month if elapsed else summary["income"]
    projected_expenses = summary["expenses"] / elapsed * days_in_month if elapsed else summary["expenses"]
    return {
        "projected_income": projected_income,
        "projected_expenses": projected_expenses,
        "projected_cash_flow": projected_income - projected_expenses,
        "remaining_days": remaining_days,
    }


def build_insights(user_id: int, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    summary = _month_summary(user_id, today)
    budget = _budget_snapshot(user_id, today)
    duplicates = _duplicate_alerts(user_id, today)
    anomalies = _anomalies(user_id, today)
    prediction = _month_end_prediction(user_id, today)
    budget_risks = []
    elapsed = today.day
    days_in_month = monthrange(today.year, today.month)[1]
    for item in budget["budgets"]:
        projected = float(item["spent"]) / elapsed * days_in_month if elapsed else 0
        if projected > float(item["limit_amount"]):
            budget_risks.append({
                "category": item["category"] or "All spending",
                "limit": float(item["limit_amount"]),
                "spent": float(item["spent"]),
                "projected": round(projected, 2),
            })
    return {
        "actual": {
            "period": f"{today.strftime('%B')} {today.year} through {today.isoformat()}",
            **{key: round(value, 2) for key, value in summary.items()},
            "budget_remaining": round(budget["remaining"], 2),
        },
        "predictions": {
            "month_end": {key: round(value, 2) if key != "remaining_days" else value for key, value in prediction.items()},
            "budget_overruns": budget_risks,
        },
        "alerts": {"anomalies": anomalies, "duplicates": duplicates},
    }


def answer_question(user_id: int, question: str, today: date | None = None) -> dict[str, Any]:
    question = " ".join(question.strip().split())
    if not question:
        raise ValueError("Ask a question about your finances.")
    if len(question) > 500:
        raise ValueError("Questions must be 500 characters or fewer.")
    today = today or date.today()
    insights = build_insights(user_id, today)
    actual = insights["actual"]
    lower = question.lower()
    amount_match = AMOUNT_PATTERN.search(question)

    if any(term in lower for term in ("duplicate", "duplicat", "same transaction")):
        duplicates = insights["alerts"]["duplicates"]
        if not duplicates:
            answer = "I found no likely duplicate expenses in the last 90 days."
        else:
            answer = f"I found {len(duplicates)} likely duplicate expense group(s) in the last 90 days."
        return {"answer": answer, "kind": "duplicate_alert", "basis": "actual", "data": duplicates, "insights": insights}

    if any(term in lower for term in ("anomal", "unusual", "suspicious")):
        anomalies = insights["alerts"]["anomalies"]
        answer = "I found no unusually large expenses in the last 90 days." if not anomalies else (
            f"I found {len(anomalies)} unusually large expense(s) compared with your last 90 days."
        )
        return {"answer": answer, "kind": "anomaly_alert", "basis": "actual", "data": anomalies, "insights": insights}

    if "budget" in lower and any(term in lower for term in ("over", "risk", "exceed", "predict")):
        risks = insights["predictions"]["budget_overruns"]
        answer = "No monthly budget is currently predicted to be exceeded." if not risks else (
            f"{len(risks)} monthly budget(s) are predicted to be exceeded if your current daily pace continues."
        )
        return {"answer": answer, "kind": "budget_prediction", "basis": "prediction", "data": risks, "insights": insights}

    if any(term in lower for term in ("month-end", "month end", "end of month", "project")):
        projected = insights["predictions"]["month_end"]
        return {
            "answer": (
                f"Projected month-end cash flow is {_money(projected['projected_cash_flow'])}. "
                "This is a prediction based on your average daily pace so far."
            ),
            "kind": "month_end_prediction",
            "basis": "prediction",
            "data": projected,
            "insights": insights,
        }

    if any(term in lower for term in ("afford", "can i buy", "can i spend")) and amount_match:
        purchase = float(amount_match.group(1).replace(",", ""))
        available = min(max(actual["cash_flow"], 0), actual["budget_remaining"]) if actual["budget_remaining"] else max(actual["cash_flow"], 0)
        can_afford = purchase <= available
        return {
            "answer": (
                f"Based on actual records this month, {'yes' if can_afford else 'not comfortably'}: "
                f"the purchase is {_money(purchase)} and your estimated available room is {_money(available)}."
            ),
            "kind": "affordability",
            "basis": "actual",
            "data": {"purchase": purchase, "available": round(available, 2), "can_afford": can_afford},
            "insights": insights,
        }

    if any(term in lower for term in ("biggest", "largest", "most expensive")):
        row = get_db().execute(
            """
            SELECT title, amount, category, vendor, transaction_date
            FROM transactions
            WHERE user_id=? AND transaction_type='expense'
            AND transaction_date BETWEEN ? AND ?
            ORDER BY amount DESC, id DESC LIMIT 1
            """,
            (user_id, *_month_window(today)),
        ).fetchone()
        if row is None:
            answer = "I found no expenses in the current month."
            data = None
        else:
            data = dict(row)
            answer = f"Your biggest expense this month is {_money(float(row['amount']))} for {row['title']}."
        return {"answer": answer, "kind": "biggest_expense", "basis": "actual", "data": data, "insights": insights}

    if any(term in lower for term in ("spend", "spent", "expense", "cash flow", "cashflow")):
        return {
            "answer": (
                f"This month you spent {_money(actual['expenses'])}, received {_money(actual['income'])}, "
                f"and your actual cash flow is {_money(actual['cash_flow'])}."
            ),
            "kind": "monthly_summary",
            "basis": "actual",
            "data": actual,
            "insights": insights,
        }

    return {
        "answer": (
            "I can answer questions about this month's spending, biggest expenses, affordability, "
            "unusual or duplicate transactions, budget-overrun predictions, and estimated month-end cash flow."
        ),
        "kind": "help",
        "basis": "actual",
        "data": None,
        "insights": insights,
    }
