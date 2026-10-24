import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import spotipy
from spotipy.exceptions import SpotifyException

from spotify_credentials import (
    CredentialValidationError,
    SpotifyCredentials,
    choose_startup_credentials,
    token_cache_path,
    validate_credentials,
)
from spotify_unfollow_gui import (
    AuthenticationFailure,
    connect_and_fetch,
    fetch_followed_artists,
    merge_visible_selection,
    spotify_error_message,
)


class CredentialValidationTests(unittest.TestCase):
    def test_rejects_missing_client_id_and_secret(self):
        with self.assertRaisesRegex(CredentialValidationError, "Client ID"):
            validate_credentials(" ", "secret")
        with self.assertRaisesRegex(CredentialValidationError, "Client Secret"):
            validate_credentials("client", " ")

    def test_trims_credentials_without_exposing_secret_in_repr(self):
        credentials = validate_credentials(" client ", " secret ")
        self.assertEqual(credentials.client_id, "client")
        self.assertNotIn("secret", repr(credentials))

    def test_environment_credentials_take_precedence(self):
        saved = SpotifyCredentials("saved-id", "saved-secret")
        credentials, source, client_id, secret = choose_startup_credentials(
            {
                "SPOTIPY_CLIENT_ID": "env-id",
                "SPOTIPY_CLIENT_SECRET": "env-secret",
            },
            saved,
        )
        self.assertEqual(credentials.client_id, "env-id")
        self.assertEqual(source, "environment")
        self.assertEqual((client_id, secret), ("env-id", "env-secret"))

    def test_saved_credentials_are_used_if_environment_is_incomplete(self):
        saved = SpotifyCredentials("saved-id", "saved-secret")
        credentials, source, _client_id, _secret = choose_startup_credentials(
            {"SPOTIPY_CLIENT_ID": "partial"}, saved
        )
        self.assertEqual(credentials, saved)
        self.assertEqual(source, "saved")

    def test_incomplete_environment_is_prefilled_without_saved_credentials(self):
        credentials, source, client_id, secret = choose_startup_credentials(
            {"SPOTIPY_CLIENT_ID": "partial"}, None
        )
        self.assertIsNone(credentials)
        self.assertEqual(source, "none")
        self.assertEqual((client_id, secret), ("partial", ""))

    def test_visible_selection_preserves_hidden_selections(self):
        self.assertEqual(
            merge_visible_selection(
                {"visible-selected", "hidden-selected"},
                {"visible-selected", "visible-unselected"},
                {"visible-unselected"},
            ),
            {"hidden-selected", "visible-unselected"},
        )

    def test_token_cache_isolated_per_client_id(self):
        with patch.dict(
            "os.environ",
            {"LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local"},
        ):
            first = token_cache_path("client-one")
            second = token_cache_path("client-two")
        self.assertNotEqual(first, second)
        self.assertNotIn("client-one", str(first))


class ArtistPaginationTests(unittest.TestCase):
    def test_fetches_all_artist_pages_using_cursor(self):
        class Client:
            def __init__(self):
                self.cursors = []
                self.pages = [
                    {
                        "artists": {
                            "items": [{"id": "one"}],
                            "cursors": {"after": "cursor-one"},
                        }
                    },
                    {
                        "artists": {
                            "items": [{"id": "two"}],
                            "cursors": None,
                        }
                    },
                ]

            def current_user_followed_artists(self, limit, after):
                self.cursors.append((limit, after))
                return self.pages.pop(0)

        client = Client()
        self.assertEqual(
            fetch_followed_artists(client), [{"id": "one"}, {"id": "two"}]
        )
        self.assertEqual(client.cursors, [(50, None), (50, "cursor-one")])


class SafeErrorMessageTests(unittest.TestCase):
    def test_error_messages_never_echo_exception_text(self):
        class SpotifyOauthError(Exception):
            pass

        error = SpotifyOauthError("sensitive-secret-value")
        message = spotify_error_message(error)
        self.assertIn("redirect URI", message)
        self.assertNotIn("sensitive-secret-value", message)

    def test_spotify_api_status_gets_actionable_error(self):
        class SpotifyException(Exception):
            http_status = 403

        message = spotify_error_message(SpotifyException("anything"))
        self.assertIn("follow-read", message)

    def test_authentication_failure_is_recoverable_and_does_not_echo_api_text(self):
        class RejectedClient:
            def current_user_followed_artists(self, **_kwargs):
                raise SpotifyException(401, -1, "private credential details")

        credentials = SpotifyCredentials("test-client", "test-secret")
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "token.cache"
            with (
                patch("spotify_unfollow_gui.token_cache_path", return_value=cache_path),
                patch("spotipy.oauth2.SpotifyOAuth") as oauth,
                patch("spotipy.Spotify", return_value=RejectedClient()),
            ):
                with self.assertRaises(AuthenticationFailure) as raised:
                    connect_and_fetch(credentials)

        self.assertIn("Client ID", str(raised.exception))
        self.assertNotIn("private credential details", str(raised.exception))
        oauth.assert_called_once()
        self.assertEqual(oauth.call_args.kwargs["client_id"], "test-client")
        self.assertEqual(oauth.call_args.kwargs["client_secret"], "test-secret")


if __name__ == "__main__":
    unittest.main()
