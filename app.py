from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, g

from auth import auth_bp, load_logged_in_user
from db import close_db, init_db
from routes import main_bp


BASE_DIR = Path(__file__).resolve().parent


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("HISABSMART_SECRET_KEY", "local-development-only-change-me"),
        DATABASE=str(BASE_DIR / "hisabsmart.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HISABSMART_SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        UPLOAD_FOLDER=str(BASE_DIR / "instance" / "uploads" / "receipts"),
        GOODS_UPLOAD_FOLDER=str(BASE_DIR / "instance" / "uploads" / "goods"),
        OCR_UPLOAD_FOLDER=str(BASE_DIR / "instance" / "uploads" / "ocr"),
        ENABLE_SCHEDULER=os.environ.get("ENABLE_SCHEDULER", "true").lower() in {"1", "true", "yes"},
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["GOODS_UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["OCR_UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    app.before_request(load_logged_in_user)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    if not app.testing and app.config.get("ENABLE_SCHEDULER", True):
        from scheduler import start_scheduler

        start_scheduler(app)

    @app.context_processor
    def inject_template_helpers():
        from auth import csrf_token

        return {"csrf_token": csrf_token}

    @app.cli.command("init-db")
    def init_db_command() -> None:
        init_db()
        print("Initialized HisabSmart database.")

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
