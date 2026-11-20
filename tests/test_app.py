from html import unescape
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

from app import create_app


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(
            "os.environ",
            {
                "FLASK_SECRET_KEY": "test-session-signing-key",
                "ENTRA_TENANT_ID": "test-tenant",
                "ENTRA_CLIENT_ID": "test-client",
                "ENTRA_CLIENT_SECRET": "test-client-secret",
                "ENTRA_REDIRECT_URI": "http://localhost:5000/auth/callback",
                "EXTERNAL_ID_TENANT_ID": "external-tenant",
                "EXTERNAL_ID_SUBDOMAIN": "external-example",
                "EXTERNAL_ID_CLIENT_ID": "external-client",
                "EXTERNAL_ID_CLIENT_SECRET": "external-secret",
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("app.load_dotenv")
        dotenv.start()
        self.addCleanup(dotenv.stop)
        self.app = create_app({"TESTING": True, "SERVER_NAME": "localhost:5000"})
        self.client = self.app.test_client()
        patcher = patch("app.msal.ConfidentialClientApplication")
        self.identity_factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.identity = self.identity_factory.return_value
        self.flow = {
            "auth_uri": "https://login.microsoftonline.com/test/authorize",
            "state": "test-state",
            "nonce": "test-nonce",
            "code_verifier": "test-code-verifier",
        }
        self.identity.initiate_auth_code_flow.return_value = self.flow

    def start_flow(self):
        with self.client.session_transaction() as session:
            session["auth_flow"] = self.flow
            session["auth_provider"] = "entra"

    def test_home_has_distinct_top_level_links_for_both_providers(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.get_data(as_text=True),
            r'<a\b[^>]*href="http://localhost:5000/login/entra"[^>]*target="_top"',
        )
        self.assertRegex(
            response.get_data(as_text=True),
            r'<a\b[^>]*href="http://localhost:5000/login/external"[^>]*target="_top"',
        )
        self.assertNotIn(b"<form", response.data)
        self.identity_factory.assert_not_called()

    def test_forwarded_redirect_uri_is_used_for_authentication(self):
        callback_uri = "https://private-forward.example/auth/callback"
        with patch.dict("os.environ", {"ENTRA_REDIRECT_URI": callback_uri}):
            app = create_app(
                {
                    "TESTING": True,
                    "TENANT_ID": "test-tenant",
                    "CLIENT_ID": "test-client",
                    "CLIENT_SECRET": "test-client-secret",
                }
            )

        response = app.test_client().get(
            "/login/start", base_url="https://private-forward.example"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.location, "https://private-forward.example/login/continue"
        )
        self.identity.initiate_auth_code_flow.assert_called_once_with(
            scopes=["User.Read"],
            redirect_uri=callback_uri,
            response_mode="query",
        )

    def test_login_redirects_and_preserves_flow(self):
        response = self.client.get("/login/start")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "http://localhost:5000/login/continue")
        continuation = self.client.get(response.location)
        self.assertEqual(continuation.status_code, 302)
        self.assertEqual(continuation.location, self.flow["auth_uri"])
        self.identity_factory.assert_called_once_with(
            "test-client",
            authority="https://login.microsoftonline.com/test-tenant",
            client_credential="test-client-secret",
        )
        self.identity.initiate_auth_code_flow.assert_called_once_with(
            scopes=["User.Read"],
            redirect_uri="http://localhost:5000/auth/callback",
            response_mode="query",
        )
        with self.client.session_transaction() as session:
            self.assertEqual(session["auth_flow"], self.flow)
            self.assertEqual(session["auth_provider"], "entra")

    def test_external_id_authority_and_signin_only_scopes(self):
        response = self.client.get("/login/external/start")

        self.assertEqual(response.status_code, 302)
        self.identity_factory.assert_called_once_with(
            "external-client",
            authority="https://external-example.ciamlogin.com/external-tenant",
            client_credential="external-secret",
        )
        self.identity.initiate_auth_code_flow.assert_called_once_with(
            scopes=[],
            redirect_uri="http://localhost:5000/auth/callback",
            response_mode="query",
        )
        with self.client.session_transaction() as session:
            self.assertEqual(session["auth_provider"], "external")

    def test_external_callback_uses_saved_provider_not_query_parameter(self):
        self.client.get("/login/external/start")
        self.identity_factory.reset_mock()
        self.identity.acquire_token_by_auth_code_flow.return_value = {
            "id_token": "external-id-token",
            "id_token_claims": {"roles": ["Federated.Employee"]},
        }

        response = self.client.get(
            "/auth/callback?state=test-state&code=external-code&provider=entra"
        )

        self.assertEqual(response.status_code, 200)
        self.identity_factory.assert_called_once_with(
            "external-client",
            authority="https://external-example.ciamlogin.com/external-tenant",
            client_credential="external-secret",
        )
        self.assertIn("External ID 認証（フェデレーション）", response.get_data(as_text=True))
        self.assertIn(b"external-id-token", response.data)
        self.assertIn(b"Federated.Employee", response.data)
        with self.client.session_transaction() as session:
            self.assertEqual(dict(session), {})

    def test_external_id_can_use_its_own_api_scopes(self):
        with patch.dict(
            "os.environ", {"EXTERNAL_ID_SCOPES": "api://external-api/read"}
        ):
            app = create_app({"TESTING": True, "SERVER_NAME": "localhost:5000"})

        app.test_client().get("/login/external/start")

        self.identity.initiate_auth_code_flow.assert_called_once_with(
            scopes=["api://external-api/read"],
            redirect_uri="http://localhost:5000/auth/callback",
            response_mode="query",
        )

    def test_unconfigured_external_provider_is_disabled_without_fallback(self):
        app = create_app(
            {
                "TESTING": True,
                "SERVER_NAME": "localhost:5000",
                "EXTERNAL_ID_TENANT_ID": "",
                "EXTERNAL_ID_SUBDOMAIN": "",
                "EXTERNAL_ID_CLIENT_ID": "",
                "EXTERNAL_ID_CLIENT_SECRET": "",
            }
        )
        client = app.test_client()

        home = client.get("/")
        response = client.get("/login/external/start")

        self.assertIn(
            '<button type="button" disabled>External ID で認証</button>',
            home.get_data(as_text=True),
        )
        self.assertNotIn(b'href="http://localhost:5000/login/external"', home.data)
        self.assertEqual(response.status_code, 503)
        self.identity_factory.assert_not_called()

    def test_external_only_configuration_does_not_require_workforce_credentials(self):
        app = create_app(
            {
                "TESTING": True,
                "SERVER_NAME": "localhost:5000",
                "TENANT_ID": "",
                "CLIENT_ID": "",
                "CLIENT_SECRET": "",
            }
        )
        client = app.test_client()

        home = client.get("/")
        direct = client.get("/login/entra/start")
        external = client.get("/login/external/start")

        self.assertIn(
            '<button type="button" disabled>Entra ID で認証</button>',
            home.get_data(as_text=True),
        )
        self.assertEqual(direct.status_code, 503)
        self.assertEqual(external.status_code, 302)
        self.identity_factory.assert_called_once_with(
            "external-client",
            authority="https://external-example.ciamlogin.com/external-tenant",
            client_credential="external-secret",
        )

    def test_ciam_authority_cannot_be_used_for_direct_entra_button(self):
        with self.assertRaisesRegex(RuntimeError, "EXTERNAL_ID_SUBDOMAIN"):
            create_app({"AUTHORITY": "https://example.ciamlogin.com/external-tenant"})

    def test_partial_external_configuration_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, "EXTERNAL_ID_CLIENT_SECRET"):
            create_app({"EXTERNAL_ID_CLIENT_SECRET": ""})

    def test_external_subdomain_does_not_accept_a_full_url(self):
        with self.assertRaisesRegex(RuntimeError, "EXTERNAL_ID_SUBDOMAIN"):
            create_app({"EXTERNAL_ID_SUBDOMAIN": "https://example.ciamlogin.com"})

    def test_unknown_provider_is_rejected(self):
        self.assertEqual(self.client.get("/login/unknown").status_code, 404)
        self.assertEqual(self.client.get("/login/unknown/start").status_code, 404)
        self.identity_factory.assert_not_called()

    def test_signing_key_preserves_session_across_app_restart(self):
        self.identity.acquire_token_by_auth_code_flow.return_value = {
            "id_token": "example-id-token",
            "id_token_claims": {"roles": ["Example.Reader"]},
        }
        for origin in ("http://localhost:5000", "https://private-forward.example"):
            with self.subTest(origin=origin), patch.dict(
                "os.environ", {"ENTRA_REDIRECT_URI": origin + "/auth/callback"}
            ):
                first_app = create_app({"TESTING": True})
                first_client = first_app.test_client()
                login = first_client.get("/login/start", base_url=origin)
                cookie_header = login.headers["Set-Cookie"]
                self.assertEqual("Secure;" in cookie_header, origin.startswith("https:"))
                self.assertIn("HttpOnly;", cookie_header)
                self.assertIn("SameSite=Lax", cookie_header)
                cookie_name = first_app.config["SESSION_COOKIE_NAME"]
                cookie = first_client.get_cookie(
                    cookie_name, domain=urlsplit(origin).hostname
                )
                self.assertIsNotNone(cookie)

                restarted_app = create_app({"TESTING": True})
                restarted_client = restarted_app.test_client()
                restarted_client.set_cookie(
                    cookie_name, cookie.value, domain=urlsplit(origin).hostname
                )
                response = restarted_client.get(
                    "/auth/callback?code=test-code&state=test-state", base_url=origin
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"example-id-token", response.data)
                self.assertIn(b"Example.Reader", response.data)
                self.assertIsNone(
                    restarted_client.get_cookie(
                        cookie_name, domain=urlsplit(origin).hostname
                    )
                )

    def test_callback_displays_tokens_without_persisting_them(self):
        self.start_flow()
        self.identity.acquire_token_by_auth_code_flow.return_value = {
            "id_token": "example-id-token",
            "access_token": "example-access-token",
            "refresh_token": "example-refresh-token",
            "id_token_claims": {"name": "Example User", "aud": "test-client"},
        }

        response = self.client.get(
            "/auth/callback?code=test-code&state=test-state"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"example-id-token", response.data)
        self.assertIn(b"example-access-token", response.data)
        self.assertIn(b"Example User", response.data)
        self.assertNotIn(b"example-refresh-token", response.data)
        self.identity.acquire_token_by_auth_code_flow.assert_called_once_with(
            self.flow, {"code": "test-code", "state": "test-state"}
        )
        with self.client.session_transaction() as session:
            self.assertEqual(dict(session), {})
        self.assertNotIn("example-", response.headers.get("Set-Cookie", ""))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(
            self.client.get("/auth/callback?code=test-code").status_code, 400
        )
        self.identity.acquire_token_by_auth_code_flow.assert_called_once()

    def test_callback_without_session_is_rejected(self):
        response = self.client.get("/auth/callback?code=test-code")

        self.assertEqual(response.status_code, 400)
        self.identity_factory.assert_not_called()

    def test_callback_rejects_invalid_state_without_consuming_flow(self):
        self.start_flow()

        for state in ("incorrect", "", "\u3042"):
            with self.subTest(state=state):
                response = self.client.get(
                    "/auth/callback", query_string={"state": state}
                )
                self.assertEqual(response.status_code, 400)
        self.identity_factory.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertEqual(session["auth_flow"], self.flow)

    def test_callback_rejects_failed_msal_verification(self):
        self.start_flow()
        self.identity.acquire_token_by_auth_code_flow.side_effect = ValueError(
            "nonce mismatch"
        )

        response = self.client.get("/auth/callback?state=test-state&code=test-code")

        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as session:
            self.assertNotIn("auth_flow", session)

    def test_login_normalizes_host_before_creating_session(self):
        response = self.client.get("/login", base_url="http://127.0.0.1:5000")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "http://localhost:5000/login/entra/start")
        self.assertNotIn("Set-Cookie", response.headers)
        self.identity_factory.assert_not_called()

    def test_https_proxy_does_not_cause_a_canonical_redirect_loop(self):
        app = create_app(
            {
                "TESTING": True,
                "REDIRECT_URI": "https://private-forward.example/auth/callback",
            }
        )
        client = app.test_client()
        login = client.get("/login", base_url="http://internal-proxy:5000")

        self.assertEqual(login.location, "https://private-forward.example/login/entra/start")
        start = client.get(
            "/login/start",
            base_url="http://internal-proxy:5000",
        )
        self.assertEqual(
            start.location, "https://private-forward.example/login/continue"
        )
        self.identity.initiate_auth_code_flow.assert_called_once()

    def test_other_flask_session_cookie_does_not_overwrite_authentication(self):
        self.client.get("/login/start")
        self.client.set_cookie("session", "another-flask-application-session")

        response = self.client.get("/login/continue")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, self.flow["auth_uri"])

    def test_login_stops_before_provider_if_cookie_is_not_returned(self):
        self.client.get("/login/start")
        self.client.delete_cookie(self.app.config["SESSION_COOKIE_NAME"])

        with self.assertLogs(self.app.logger, level="WARNING") as logs:
            response = self.client.get("/login/continue")

        self.assertEqual(response.status_code, 400)
        self.assertIn("cookie_not_sent", logs.output[0])
        self.assertNotIn("Location", response.headers)

    def test_callback_recovers_cookie_withheld_on_cross_site_navigation(self):
        self.client.get("/login/start")
        unrelated_client = self.app.test_client()
        response = unrelated_client.get(
            "/auth/callback?code=test-code&state=test-state"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"resume-auth.js", response.data)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.identity.acquire_token_by_auth_code_flow.assert_not_called()
        html = response.get_data(as_text=True)
        retry_url = unescape(html.split('id="resume-auth" href="')[1].split('"')[0])
        self.assertIn("_cookie_retry=1", retry_url)
        self.identity.acquire_token_by_auth_code_flow.return_value = {
            "id_token": "example-id-token",
            "id_token_claims": {"name": "Example User"},
        }

        result = self.client.get(retry_url)

        self.assertEqual(result.status_code, 200)
        self.assertIn(b"example-id-token", result.data)
        self.identity.acquire_token_by_auth_code_flow.assert_called_once_with(
            self.flow, {"code": "test-code", "state": "test-state"}
        )

    def test_callback_without_cookie_cannot_retry_forever_or_authenticate(self):
        response = self.client.get(
            "/auth/callback?code=test-code&state=test-state&_cookie_retry=1"
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"resume-auth.js", response.data)
        self.identity_factory.assert_not_called()

    def test_invalid_cookie_is_distinguished_from_missing_cookie(self):
        self.client.set_cookie(
            self.app.config["SESSION_COOKIE_NAME"], "invalid-signed-cookie"
        )

        with self.assertLogs(self.app.logger, level="WARNING") as logs:
            response = self.client.get(
                "/auth/callback?code=test-code&state=test-state&_cookie_retry=1"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid_cookie", logs.output[0])
        self.assertNotIn("invalid-signed-cookie", "".join(logs.output))
        self.assertNotIn("test-code", "".join(logs.output))

    def test_denied_consent_is_displayed_and_escaped(self):
        self.start_flow()
        self.identity.acquire_token_by_auth_code_flow.return_value = {
            "error": "access_denied",
            "error_description": "<script>alert('denied')</script>",
        }

        response = self.client.get(
            "/auth/callback?error=access_denied&state=test-state"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"<script>", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_token_request_network_failure_is_displayed(self):
        self.start_flow()
        self.identity.acquire_token_by_auth_code_flow.side_effect = (
            requests.ConnectionError("connection failed")
        )

        response = self.client.get("/auth/callback?state=test-state")

        self.assertEqual(response.status_code, 502)

    def test_login_failures_are_displayed_without_starting_session(self):
        for error in (
            requests.ConnectionError("connection failed"),
            ValueError("invalid authority"),
        ):
            with self.subTest(error=type(error).__name__):
                self.identity.initiate_auth_code_flow.side_effect = error

                response = self.client.get("/login/start")

                self.assertEqual(response.status_code, 502)
                with self.client.session_transaction() as session:
                    self.assertNotIn("auth_flow", session)

    def test_missing_id_token_is_not_treated_as_success(self):
        self.start_flow()
        self.identity.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "example-access-token"
        }

        response = self.client.get("/auth/callback?state=test-state")

        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b"example-access-token", response.data)

    def test_missing_configuration_fails_at_startup(self):
        for config_name, environment_name in (
            ("SECRET_KEY", "FLASK_SECRET_KEY"),
            ("TENANT_ID", "ENTRA_TENANT_ID"),
            ("CLIENT_ID", "ENTRA_CLIENT_ID"),
            ("CLIENT_SECRET", "ENTRA_CLIENT_SECRET"),
            ("REDIRECT_URI", "ENTRA_REDIRECT_URI"),
        ):
            with self.subTest(setting=config_name):
                with self.assertRaisesRegex(RuntimeError, environment_name):
                    create_app({config_name: ""})

    def test_redirect_uri_requires_https_except_on_loopback(self):
        for uri in (
            "http://private-forward.example/auth/callback",
            "/auth/callback",
            "https:///auth/callback",
            "https://example.com/auth/callback#fragment",
        ):
            with self.subTest(uri=uri):
                with self.assertRaisesRegex(RuntimeError, "ENTRA_REDIRECT_URI"):
                    create_app({"REDIRECT_URI": uri})


if __name__ == "__main__":
    unittest.main()
