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
            },
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("app.load_dotenv")
        dotenv.start()
        self.addCleanup(dotenv.stop)
        self.app = create_app({"TESTING": True})
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

    def test_home_starts_authentication_with_top_level_link_not_form(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.get_data(as_text=True),
            r'<a\b[^>]*href="/login"[^>]*target="_top"',
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

        response = app.test_client().get("/login")

        self.assertEqual(response.status_code, 302)
        self.identity.initiate_auth_code_flow.assert_called_once_with(
            scopes=["User.Read"],
            redirect_uri=callback_uri,
            response_mode="query",
        )

    def test_login_redirects_and_preserves_flow(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, self.flow["auth_uri"])
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

    def test_external_id_authority_and_signin_only_scopes(self):
        authority = "https://example.ciamlogin.com/test-external-tenant"
        with patch.dict(
            "os.environ", {"ENTRA_AUTHORITY": authority, "ENTRA_SCOPES": ""}
        ):
            app = create_app({"TESTING": True})

        response = app.test_client().get("/login")

        self.assertEqual(response.status_code, 302)
        self.identity_factory.assert_called_once_with(
            "test-client",
            authority=authority,
            client_credential="test-client-secret",
        )
        self.identity.initiate_auth_code_flow.assert_called_once_with(
            scopes=[],
            redirect_uri="http://localhost:5000/auth/callback",
            response_mode="query",
        )

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
                login = first_client.get("/login", base_url=origin)
                cookie_header = login.headers["Set-Cookie"]
                self.assertEqual("Secure;" in cookie_header, origin.startswith("https:"))
                self.assertIn("HttpOnly;", cookie_header)
                self.assertIn("SameSite=Lax", cookie_header)
                cookie = first_client.get_cookie(
                    "session", domain=urlsplit(origin).hostname
                )
                self.assertIsNotNone(cookie)

                restarted_app = create_app({"TESTING": True})
                restarted_client = restarted_app.test_client()
                restarted_client.set_cookie(
                    "session", cookie.value, domain=urlsplit(origin).hostname
                )
                response = restarted_client.get(
                    "/auth/callback?code=test-code&state=test-state", base_url=origin
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"example-id-token", response.data)
                self.assertIn(b"Example.Reader", response.data)
                self.assertIsNone(
                    restarted_client.get_cookie(
                        "session", domain=urlsplit(origin).hostname
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

    def test_callback_rejects_invalid_authentication_response(self):
        self.start_flow()
        self.identity.acquire_token_by_auth_code_flow.side_effect = ValueError(
            "state mismatch"
        )

        response = self.client.get("/auth/callback?state=incorrect")

        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as session:
            self.assertNotIn("auth_flow", session)

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

                response = self.client.get("/login")

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
