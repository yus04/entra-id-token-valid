import os
from urllib.parse import urlsplit

import msal
import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        TENANT_ID=os.getenv("ENTRA_TENANT_ID"),
        CLIENT_ID=os.getenv("ENTRA_CLIENT_ID"),
        CLIENT_SECRET=os.getenv("ENTRA_CLIENT_SECRET"),
        REDIRECT_URI=os.getenv("ENTRA_REDIRECT_URI"),
        AUTHORITY=os.getenv("ENTRA_AUTHORITY"),
        SCOPES=os.getenv("ENTRA_SCOPES", "User.Read").split(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config is not None:
        app.config.update(test_config)

    missing = [
        environment_name
        for config_name, environment_name in (
            ("SECRET_KEY", "FLASK_SECRET_KEY"),
            ("TENANT_ID", "ENTRA_TENANT_ID"),
            ("CLIENT_ID", "ENTRA_CLIENT_ID"),
            ("CLIENT_SECRET", "ENTRA_CLIENT_SECRET"),
            ("REDIRECT_URI", "ENTRA_REDIRECT_URI"),
        )
        if not app.config[config_name]
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    callback_url = urlsplit(app.config["REDIRECT_URI"])
    if (
        not callback_url.hostname
        or callback_url.username is not None
        or callback_url.password is not None
        or callback_url.query
        or callback_url.fragment
        or not (
            callback_url.scheme == "https"
            or (
                callback_url.scheme == "http"
                and callback_url.hostname in ("localhost", "127.0.0.1", "::1")
            )
        )
    ):
        raise RuntimeError(
            "ENTRA_REDIRECT_URI must be an absolute HTTPS URL without "
            "credentials, query or fragment (HTTP is allowed only on loopback)."
        )
    app.config["SESSION_COOKIE_SECURE"] = callback_url.scheme == "https"

    def identity_client():
        return msal.ConfidentialClientApplication(
            app.config["CLIENT_ID"],
            authority=app.config["AUTHORITY"] or (
                "https://login.microsoftonline.com/"
                f"{app.config['TENANT_ID']}"
            ),
            client_credential=app.config["CLIENT_SECRET"],
        )

    def error_page(message, status):
        return render_template("result.html", error=message), status

    @app.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/login")
    def login():
        session.clear()
        try:
            flow = identity_client().initiate_auth_code_flow(
                scopes=app.config["SCOPES"],
                redirect_uri=app.config["REDIRECT_URI"],
                response_mode="query",
            )
        except requests.RequestException:
            return error_page("Entra ID に接続できません。接続を確認してください。", 502)
        except ValueError:
            return error_page(
                "Entra ID のテナント ID・Authority・アプリ登録設定を確認してください。",
                502,
            )

        if "auth_uri" not in flow:
            return error_page("認証 URL を取得できませんでした。", 502)
        session["auth_flow"] = flow
        return redirect(flow["auth_uri"])

    @app.get("/auth/callback")
    def callback():
        flow = session.pop("auth_flow", None)
        if flow is None:
            return error_page(
                "認証セッションがありません。認証開始時と同じホスト名で開き、"
                "Cookie と FLASK_SECRET_KEY の設定を確認して、最初から認証してください。",
                400,
            )

        try:
            result = identity_client().acquire_token_by_auth_code_flow(
                flow, request.args.to_dict()
            )
        except ValueError:
            return error_page("認証応答を検証できませんでした。最初から認証してください。", 400)
        except requests.RequestException:
            return error_page("トークンを取得できません。接続を確認してください。", 502)

        if "error" in result:
            return error_page(
                result.get("error_description") or result["error"], 400
            )
        if not result.get("id_token"):
            return error_page("ID トークンが返されませんでした。", 502)

        # Never put the token response in Flask's client-side session cookie.
        return render_template("result.html", result=result)

    return app


if __name__ == "__main__":
    create_app().run(
        host="127.0.0.1", port=5000, debug=False, load_dotenv=False
    )
