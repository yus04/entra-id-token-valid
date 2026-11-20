import hashlib
import os
import re
import secrets
from urllib.parse import urlencode, urlsplit

import msal
import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for


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
        EXTERNAL_ID_TENANT_ID=os.getenv("EXTERNAL_ID_TENANT_ID"),
        EXTERNAL_ID_SUBDOMAIN=os.getenv("EXTERNAL_ID_SUBDOMAIN"),
        EXTERNAL_ID_CLIENT_ID=os.getenv("EXTERNAL_ID_CLIENT_ID"),
        EXTERNAL_ID_CLIENT_SECRET=os.getenv("EXTERNAL_ID_CLIENT_SECRET"),
        EXTERNAL_ID_SCOPES=os.getenv("EXTERNAL_ID_SCOPES", "").split(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config is not None:
        app.config.update(test_config)

    missing = [
        environment_name
        for config_name, environment_name in (
            ("SECRET_KEY", "FLASK_SECRET_KEY"),
            ("REDIRECT_URI", "ENTRA_REDIRECT_URI"),
        )
        if not app.config[config_name]
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    providers = {
        "entra": {
            "label": "Entra ID 認証（直接）",
            "client_id": app.config["CLIENT_ID"],
            "client_secret": app.config["CLIENT_SECRET"],
            "authority": app.config["AUTHORITY"] or (
                "https://login.microsoftonline.com/"
                f"{app.config['TENANT_ID']}"
            ),
            "scopes": app.config["SCOPES"],
        },
        "external": {
            "label": "External ID 認証（フェデレーション）",
            "client_id": app.config["EXTERNAL_ID_CLIENT_ID"],
            "client_secret": app.config["EXTERNAL_ID_CLIENT_SECRET"],
            "authority": (
                f"https://{app.config['EXTERNAL_ID_SUBDOMAIN']}.ciamlogin.com/"
                f"{app.config['EXTERNAL_ID_TENANT_ID']}"
            ),
            "scopes": app.config["EXTERNAL_ID_SCOPES"],
        },
    }
    for provider, fields in (
        (
            "entra",
            {
                "TENANT_ID": "ENTRA_TENANT_ID",
                "CLIENT_ID": "ENTRA_CLIENT_ID",
                "CLIENT_SECRET": "ENTRA_CLIENT_SECRET",
            },
        ),
        (
            "external",
            {
                name: name
                for name in (
                    "EXTERNAL_ID_TENANT_ID",
                    "EXTERNAL_ID_SUBDOMAIN",
                    "EXTERNAL_ID_CLIENT_ID",
                    "EXTERNAL_ID_CLIENT_SECRET",
                )
            },
        ),
    ):
        enabled = any(app.config[name] for name in fields)
        providers[provider]["enabled"] = enabled
        missing = [env for name, env in fields.items() if not app.config[name]]
        if enabled and missing:
            raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    if not any(provider["enabled"] for provider in providers.values()):
        raise RuntimeError("Configure ENTRA_* or EXTERNAL_ID_* authentication settings.")
    if app.config["AUTHORITY"]:
        authority_url = urlsplit(app.config["AUTHORITY"])
        if (
            authority_url.scheme != "https"
            or authority_url.hostname != "login.microsoftonline.com"
        ):
            raise RuntimeError(
                "ENTRA_AUTHORITY is only for direct Entra ID authentication. "
                "For CIAM, move the external tenant credentials to EXTERNAL_ID_* "
                "and set EXTERNAL_ID_SUBDOMAIN instead."
            )
    if providers["external"]["enabled"] and not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?",
        app.config["EXTERNAL_ID_SUBDOMAIN"],
    ):
        raise RuntimeError(
            "EXTERNAL_ID_SUBDOMAIN must contain only the tenant subdomain, "
            "not a URL or the .ciamlogin.com suffix."
        )

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
    cookie_scope = (
        f"{app.config['CLIENT_ID']}:{app.config['EXTERNAL_ID_CLIENT_ID']}:"
        f"{app.config['REDIRECT_URI']}"
    )
    app.config["SESSION_COOKIE_NAME"] = (
        "entra_auth_" + hashlib.sha256(cookie_scope.encode()).hexdigest()[:12]
    )
    login_uri = f"{callback_url.scheme}://{callback_url.netloc}/login"

    def identity_client(provider):
        settings = providers[provider]
        return msal.ConfidentialClientApplication(
            settings["client_id"],
            authority=settings["authority"],
            client_credential=settings["client_secret"],
        )

    def error_page(message, status):
        return render_template("result.html", error=message), status

    def session_error(stage):
        cookie_name = app.config["SESSION_COOKIE_NAME"]
        if cookie_name not in request.cookies:
            reason = "cookie_not_sent"
            message = (
                "認証用 Cookie が送信されていません。アプリの認証ボタンから開始し、"
                "ブラウザーの Cookie を許可して通常のタブで開いてください。"
            )
        elif not session:
            reason = "invalid_cookie"
            message = (
                "認証用 Cookie を復元できません。すべてのプロセスで同じ"
                " FLASK_SECRET_KEY を使用し、最初から認証してください。"
            )
        else:
            reason = "flow_not_found"
            message = "認証情報が失われたか処理済みです。最初から認証してください。"
        app.logger.warning(
            "Authentication session unavailable: stage=%s reason=%s", stage, reason
        )
        return error_page(message, 400)

    @app.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; script-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            login_uri=login_uri,
            entra_enabled=providers["entra"]["enabled"],
            external_enabled=providers["external"]["enabled"],
        )

    @app.get("/login")
    def legacy_login():
        return login("entra")

    @app.get("/login/<provider>")
    def login(provider):
        if provider not in providers:
            return error_page("不明な認証先です。", 404)
        return redirect(f"{login_uri}/{provider}/start")

    @app.get("/login/start")
    def legacy_start_login():
        return start_login("entra")

    @app.get("/login/<provider>/start")
    def start_login(provider):
        if provider not in providers:
            return error_page("不明な認証先です。", 404)
        settings = providers[provider]
        if not settings["enabled"]:
            return error_page(
                f"{settings['label']} のアプリ登録設定がありません。.env を設定してください。",
                503,
            )
        session.clear()
        try:
            flow = identity_client(provider).initiate_auth_code_flow(
                scopes=settings["scopes"],
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
        session["auth_provider"] = provider
        return redirect(f"{login_uri}/continue")

    @app.get("/login/continue")
    def continue_login():
        flow = session.get("auth_flow")
        if flow is None:
            return session_error("before_redirect")
        return redirect(flow["auth_uri"])

    @app.get("/auth/callback")
    def callback():
        flow = session.get("auth_flow")
        if flow is None:
            if (
                request.args.get("state")
                and ("code" in request.args or "error" in request.args)
                and request.args.get("_cookie_retry") != "1"
            ):
                parameters = request.args.to_dict()
                parameters["_cookie_retry"] = "1"
                retry_url = f"{url_for('callback')}?{urlencode(parameters)}"
                # Do not clear a cookie withheld on the cross-site callback.
                app.logger.warning(
                    "Authentication cookie unavailable on callback; "
                    "retrying in first-party context"
                )
                return render_template("resume_auth.html", retry_url=retry_url)
            return session_error("callback")

        if not secrets.compare_digest(
            request.args.get("state", "").encode(), flow["state"].encode()
        ):
            app.logger.warning("Authentication response rejected: state_mismatch")
            return error_page(
                "認証応答の state が一致しません。アプリの認証ボタンから開始してください。",
                400,
            )

        provider = session.get("auth_provider")
        if provider not in providers or not providers[provider]["enabled"]:
            app.logger.warning("Authentication response rejected: provider_not_found")
            return error_page(
                "認証先の情報がありません。最初の画面から認証し直してください。", 400
            )

        session.pop("auth_flow")
        session.pop("auth_provider")
        auth_response = request.args.to_dict()
        auth_response.pop("_cookie_retry", None)
        try:
            result = identity_client(provider).acquire_token_by_auth_code_flow(
                flow, auth_response
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
        return render_template(
            "result.html", result=result, provider_label=providers[provider]["label"]
        )

    return app


if __name__ == "__main__":
    create_app().run(
        host="127.0.0.1", port=5000, debug=False, load_dotenv=False
    )
