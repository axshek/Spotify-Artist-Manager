from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from spotify_credentials import (
    CredentialStoreError,
    CredentialValidationError,
    SpotifyCredentials,
    WindowsCredentialStore,
    choose_startup_credentials,
    token_cache_path,
    validate_credentials,
)


APP_TITLE = "Spotify Artist Manager"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPES = "user-follow-read user-follow-modify"
PAGE_SIZE = 50

COLORS = {
    "window": "#0d0f0e",
    "surface": "#141715",
    "surface_raised": "#191d1a",
    "hover": "#202622",
    "separator": "#272d29",
    "text": "#e6eae7",
    "secondary": "#9ba39d",
    "muted": "#68716b",
    "green": "#1db954",
    "green_hover": "#25c765",
    "green_dim": "#183a25",
    "error": "#e28b86",
    "success": "#88d29b",
}


class AuthenticationFailure(RuntimeError):
    pass


def spotify_error_message(error: Exception) -> str:
    error_name = type(error).__name__
    if error_name == "SpotifyOauthError":
        return (
            "Spotify could not complete authorization. Check the Client ID and "
            "Client Secret, and confirm the app redirect URI is exactly "
            f"{REDIRECT_URI}."
        )

    status = getattr(error, "http_status", None)
    if status == 400:
        return (
            "Spotify rejected the OAuth request. Verify your app credentials and "
            f"the redirect URI ({REDIRECT_URI}) in the Spotify Developer Dashboard."
        )
    if status == 401:
        return (
            "Spotify rejected the credentials or authorization token. Check the "
            "Client ID and Client Secret, then connect again."
        )
    if status == 403:
        return (
            "Spotify denied access. Make sure authorization was completed and the "
            "requested follow-read and follow-modify scopes are allowed."
        )
    if status == 429:
        return "Spotify is rate-limiting requests. Wait a moment and try again."
    if isinstance(status, int) and status >= 500:
        return "Spotify is temporarily unavailable. Try again shortly."

    module_name = type(error).__module__
    if module_name.startswith("requests.") or isinstance(error, (TimeoutError, ConnectionError)):
        if error_name == "Timeout":
            return "Spotify did not respond in time. Check your connection and try again."
        return "Could not reach Spotify. Check your internet connection and try again."
    return "The Spotify request failed unexpectedly. Please try again."


def fetch_followed_artists(client: Any) -> list[dict[str, Any]]:
    artists: list[dict[str, Any]] = []
    after = None
    while True:
        response = client.current_user_followed_artists(limit=PAGE_SIZE, after=after)
        artist_page = response["artists"]
        artists.extend(artist_page["items"])
        cursors = artist_page.get("cursors")
        after = cursors.get("after") if cursors else None
        if not after:
            return artists


def merge_visible_selection(
    selected_ids: set[str],
    visible_ids: set[str],
    current_visible_selection: set[str],
) -> set[str]:
    return (selected_ids - visible_ids) | current_visible_selection


def connect_and_fetch(
    credentials: SpotifyCredentials,
) -> tuple[Any, list[dict[str, Any]]]:
    try:
        import spotipy
        from spotipy.exceptions import SpotifyException, SpotifyOauthError
        from spotipy.oauth2 import SpotifyOAuth
        import requests
    except ImportError as error:
        raise AuthenticationFailure(
            "Spotipy is not installed in this Python environment. Install the project "
            "dependencies with: python -m pip install -r requirements.txt"
        ) from error

    cache_path = token_cache_path(credentials.client_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    auth_manager = SpotifyOAuth(
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SPOTIFY_SCOPES,
        cache_path=str(cache_path),
        open_browser=True,
    )
    client = spotipy.Spotify(
        auth_manager=auth_manager,
        requests_timeout=15,
        retries=2,
    )

    try:
        return client, fetch_followed_artists(client)
    except (SpotifyException, SpotifyOauthError, requests.exceptions.RequestException) as error:
        raise AuthenticationFailure(spotify_error_message(error)) from None
    except (KeyError, TypeError, ValueError):
        raise AuthenticationFailure(
            "Spotify returned an unexpected response while loading followed artists. "
            "Try again."
        ) from None


class CredentialDialog:
    def __init__(
        self,
        app: SpotifyArtistManager,
        client_id: str = "",
        client_secret: str = "",
        error_text: str = "",
        remember: bool = False,
        saved_credentials_available: bool = False,
    ) -> None:
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title(APP_TITLE)
        self.window.configure(bg=COLORS["window"])
        self.window.resizable(True, True)
        self.window.minsize(460, 470)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        if app.root.state() != "withdrawn":
            self.window.transient(app.root)
        self.client_id = tk.StringVar(value=client_id)
        self.client_secret = tk.StringVar(value=client_secret)
        self.remember = tk.BooleanVar(value=remember and app.credential_store.available)
        self.error_text = tk.StringVar(value=error_text)
        self.secret_visible = False
        self.saved_credentials_available = saved_credentials_available
        self._build()
        self.window.update_idletasks()
        app.center_window(self.window, 520, 540)
        self.window.deiconify()
        self.window.grab_set()

    def _build(self) -> None:
        frame = ttk.Frame(self.window, style="Dialog.TFrame", padding=(30, 26, 30, 24))
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="SPOTIFY DEVELOPER APP",
            style="Eyebrow.TLabel",
        ).pack(anchor="w")
        ttk.Label(frame, text=APP_TITLE, style="DialogTitle.TLabel").pack(
            anchor="w", pady=(8, 5)
        )
        ttk.Label(
            frame,
            text=(
                "Connect with your Spotify Developer credentials. Spotify will open "
                "in your browser to authorize access; your Spotify password is never "
                "requested here."
            ),
            style="DialogBody.TLabel",
            wraplength=435,
            justify="left",
        ).pack(anchor="w", pady=(0, 22))

        ttk.Label(frame, text="Client ID", style="FieldLabel.TLabel").pack(
            anchor="w", pady=(0, 7)
        )
        self.client_entry = ttk.Entry(
            frame, textvariable=self.client_id, style="Dark.TEntry"
        )
        self.client_entry.pack(fill="x", ipady=7, pady=(0, 16))

        ttk.Label(frame, text="Client Secret", style="FieldLabel.TLabel").pack(
            anchor="w", pady=(0, 7)
        )
        secret_row = ttk.Frame(frame, style="Dialog.TFrame")
        secret_row.pack(fill="x", pady=(0, 8))
        self.secret_entry = ttk.Entry(
            secret_row,
            textvariable=self.client_secret,
            style="Dark.TEntry",
            show="•",
        )
        self.secret_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.show_button = ttk.Button(
            secret_row,
            text="Show",
            style="Quiet.TButton",
            command=self.toggle_secret,
        )
        self.show_button.pack(side="left", padx=(8, 0), ipady=3)

        ttk.Checkbutton(
            frame,
            text="Remember credentials on this computer",
            variable=self.remember,
            style="Dark.TCheckbutton",
            state="normal" if self.app.credential_store.available else "disabled",
        ).pack(anchor="w", pady=(6, 0))
        if not self.app.credential_store.available:
            ttk.Label(
                frame,
                text="Secure credential storage is available on Windows only.",
                style="Muted.TLabel",
            ).pack(anchor="w", pady=(5, 0))

        self.error_label = ttk.Label(
            frame,
            textvariable=self.error_text,
            style="Error.TLabel",
            wraplength=435,
            justify="left",
        )
        self.error_label.pack(anchor="w", fill="x", pady=(15, 0))

        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=440)
        self.progress.pack(fill="x", pady=(11, 0))
        self.progress.pack_forget()

        actions = ttk.Frame(frame, style="Dialog.TFrame")
        actions.pack(fill="x", side="bottom", pady=(22, 0))
        self.cancel_button = ttk.Button(
            actions, text="Cancel", style="Quiet.TButton", command=self.cancel
        )
        self.cancel_button.pack(side="right")
        self.connect_button = ttk.Button(
            actions,
            text="Connect to Spotify",
            style="Primary.TButton",
            command=self.connect,
        )
        self.connect_button.pack(side="right", padx=(0, 8))
        self.forget_button = ttk.Button(
            actions,
            text="Forget saved credentials",
            style="Quiet.TButton",
            command=self.forget_saved,
        )
        if self.saved_credentials_available:
            self.forget_button.pack(side="left")

        self.client_entry.focus_set()
        self.window.bind("<Return>", lambda _event: self.connect())
        self.window.bind("<Escape>", lambda _event: self.cancel())

    def toggle_secret(self) -> None:
        self.secret_visible = not self.secret_visible
        self.secret_entry.configure(show="" if self.secret_visible else "•")
        self.show_button.configure(text="Hide" if self.secret_visible else "Show")

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.client_entry.configure(state=state)
        self.secret_entry.configure(state=state)
        self.connect_button.configure(state=state)
        self.cancel_button.configure(state="normal")
        self.forget_button.configure(state=state)
        if busy:
            self.progress.pack(fill="x", pady=(11, 0))
            self.progress.start(12)
            self.error_text.set("Opening Spotify authorization and loading your artists…")
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def connect(self) -> None:
        try:
            credentials = validate_credentials(
                self.client_id.get(), self.client_secret.get()
            )
        except CredentialValidationError as error:
            self.error_text.set(str(error))
            return

        if not self.remember.get() and self.app.credential_store.available:
            try:
                self.app.credential_store.delete()
                self.saved_credentials_available = False
                self.app._saved_credentials = None
                self.forget_button.pack_forget()
            except CredentialStoreError as error:
                self.error_text.set(str(error))
                return

        self.app.begin_authentication(
            credentials, self, remember=self.remember.get(), startup=False
        )

    def forget_saved(self) -> None:
        try:
            self.app.credential_store.delete()
        except CredentialStoreError as error:
            self.error_text.set(str(error))
            return
        self.saved_credentials_available = False
        self.app._saved_credentials = None
        self.forget_button.pack_forget()
        self.client_id.set("")
        self.client_secret.set("")
        self.remember.set(False)
        self.error_text.set("Saved credentials have been removed.")
        self.client_entry.focus_set()

    def cancel(self) -> None:
        self.window.destroy()
        self.app.cancel_authentication(self)

    def show_error(self, message: str) -> None:
        self.set_busy(False)
        self.error_text.set(message)
        self.client_entry.focus_set()

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()


class ConnectingDialog:
    def __init__(self, app: SpotifyArtistManager) -> None:
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title(APP_TITLE)
        self.window.configure(bg=COLORS["window"])
        self.window.resizable(False, False)
        if app.root.state() != "withdrawn":
            self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

        frame = ttk.Frame(self.window, style="Dialog.TFrame", padding=(28, 24))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_TITLE, style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Connecting securely with Spotify…",
            style="DialogBody.TLabel",
        ).pack(anchor="w", pady=(8, 14))
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=330)
        self.progress.pack(fill="x")
        self.progress.start(12)
        self.cancel_button = ttk.Button(
            frame, text="Cancel", style="Quiet.TButton", command=self.cancel
        )
        self.cancel_button.pack(anchor="e", pady=(16, 0))
        self.window.update_idletasks()
        app.center_window(self.window, 390, 180)
        self.window.deiconify()
        self.window.grab_set()

    def cancel(self) -> None:
        self.progress.stop()
        self.window.destroy()
        self.app.cancel_authentication(self)

    def close(self) -> None:
        self.progress.stop()
        if self.window.winfo_exists():
            self.window.destroy()


class SpotifyArtistManager:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.credential_store = WindowsCredentialStore()
        self.client: Any = None
        self.artists: list[dict[str, Any]] = []
        self.visible_artists: list[dict[str, Any]] = []
        self.selected_ids: set[str] = set()
        self._events: queue.Queue[tuple[str, int, Any]] = queue.Queue()
        self._auth_serial = 0
        self._auth_in_progress = False
        self._auth_cancelled: set[int] = set()
        self._dialog: CredentialDialog | ConnectingDialog | None = None
        self._saved_credentials: SpotifyCredentials | None = None
        self._hovered_index: int | None = None
        self._work_in_progress = False
        self._configure_styles()
        self._build_main_window()
        self.root.withdraw()
        self.root.after(100, self._process_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.option_add("*Font", ("Segoe UI", 10))
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=COLORS["window"])
        style.configure("Panel.TFrame", background=COLORS["surface"])
        style.configure("Dialog.TFrame", background=COLORS["surface"])
        style.configure(
            "TLabel", background=COLORS["window"], foreground=COLORS["text"]
        )
        style.configure(
            "Muted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"]
        )
        style.configure(
            "Secondary.TLabel",
            background=COLORS["window"],
            foreground=COLORS["secondary"],
        )
        style.configure(
            "Eyebrow.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "DialogTitle.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI", 17, "bold"),
        )
        style.configure(
            "DialogBody.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["secondary"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "FieldLabel.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Error.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["error"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["window"],
            foreground=COLORS["secondary"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Dark.TEntry",
            fieldbackground=COLORS["surface_raised"],
            foreground=COLORS["text"],
            insertcolor=COLORS["text"],
            bordercolor=COLORS["separator"],
            lightcolor=COLORS["separator"],
            darkcolor=COLORS["separator"],
            padding=(10, 7),
            relief="flat",
        )
        style.map(
            "Dark.TEntry",
            bordercolor=[("focus", COLORS["green"])],
            lightcolor=[("focus", COLORS["green"])],
            darkcolor=[("focus", COLORS["green"])],
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["green"],
            foreground="#071109",
            borderwidth=0,
            padding=(14, 9),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", COLORS["separator"]),
                ("pressed", "#149947"),
                ("active", COLORS["green_hover"]),
            ],
            foreground=[("disabled", COLORS["muted"])],
        )
        style.configure(
            "Quiet.TButton",
            background=COLORS["surface_raised"],
            foreground=COLORS["secondary"],
            borderwidth=0,
            padding=(11, 8),
            font=("Segoe UI", 9),
        )
        style.map(
            "Quiet.TButton",
            background=[("pressed", COLORS["hover"]), ("active", COLORS["hover"])],
            foreground=[("active", COLORS["text"])],
        )
        style.configure(
            "Dark.TCheckbutton",
            background=COLORS["surface"],
            foreground=COLORS["secondary"],
            font=("Segoe UI", 9),
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", COLORS["surface"])],
            foreground=[("selected", COLORS["text"]), ("active", COLORS["text"])],
        )
        style.configure(
            "TProgressbar",
            background=COLORS["green"],
            troughcolor=COLORS["surface_raised"],
            bordercolor=COLORS["surface_raised"],
            lightcolor=COLORS["green"],
            darkcolor=COLORS["green"],
            thickness=3,
        )

    def _build_main_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=COLORS["window"])
        self.root.minsize(730, 520)
        self.root.geometry("900x680")
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        header = tk.Frame(self.root, bg=COLORS["window"], padx=28, pady=18)
        header.pack(fill="x")
        brand = tk.Frame(header, bg=COLORS["window"])
        brand.pack(side="left")
        icon = tk.Canvas(
            brand,
            width=27,
            height=27,
            bg=COLORS["window"],
            highlightthickness=0,
        )
        icon.pack(side="left", padx=(0, 10))
        icon.create_oval(2, 2, 25, 25, fill=COLORS["green"], outline="")
        icon.create_arc(
            7, 8, 20, 14, start=205, extent=130, style="arc", outline="#0d2615", width=1.5
        )
        icon.create_arc(
            8, 12, 19, 17, start=205, extent=130, style="arc", outline="#0d2615", width=1.4
        )
        icon.create_arc(
            10, 16, 17, 20, start=205, extent=130, style="arc", outline="#0d2615", width=1.2
        )
        ttk.Label(
            brand,
            text=APP_TITLE,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        self.connection_dot = tk.Canvas(
            header, width=10, height=10, bg=COLORS["window"], highlightthickness=0
        )
        self.connection_dot.pack(side="right", padx=(7, 5))
        self.connection_dot.create_oval(1, 1, 9, 9, fill=COLORS["muted"], outline="")
        self.connection_label = ttk.Label(
            header, text="Not connected", style="Secondary.TLabel"
        )
        self.connection_label.pack(side="right")
        self.credentials_button = ttk.Button(
            header,
            text="Credentials",
            style="Quiet.TButton",
            command=self.open_credentials,
        )
        self.credentials_button.pack(side="right", padx=(0, 16))
        self.header_separator = tk.Frame(self.root, height=1, bg=COLORS["separator"])
        self.header_separator.pack(fill="x")

        content = tk.Frame(self.root, bg=COLORS["window"], padx=28, pady=22)
        content.pack(fill="both", expand=True)

        title_row = tk.Frame(content, bg=COLORS["window"])
        title_row.pack(fill="x", pady=(0, 13))
        ttk.Label(
            title_row,
            text="Followed artists",
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        self.artist_count_label = ttk.Label(
            title_row, text="", style="Secondary.TLabel"
        )
        self.artist_count_label.pack(side="left", padx=(10, 0), pady=(3, 0))
        self.reload_button = ttk.Button(
            title_row,
            text="Reload",
            style="Quiet.TButton",
            command=self.reload_artists,
        )
        self.reload_button.pack(side="right")

        search_frame = tk.Frame(
            content, bg=COLORS["surface_raised"], highlightthickness=1,
            highlightbackground=COLORS["separator"],
        )
        search_frame.pack(fill="x", pady=(0, 13))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(
            search_frame, textvariable=self.search_var, style="Dark.TEntry"
        )
        self.search_entry.pack(fill="x", padx=1, pady=1, ipady=2)
        self.search_placeholder = ttk.Label(
            search_frame,
            text="Search artists",
            style="Muted.TLabel",
            cursor="xterm",
        )
        self.search_placeholder.place(x=12, y=8)
        self.search_placeholder.bind("<Button-1>", lambda _event: self.search_entry.focus_set())
        self.search_var.trace_add("write", self._on_search_change)
        self.search_entry.bind("<FocusIn>", self._hide_search_placeholder)
        self.search_entry.bind("<FocusOut>", self._show_search_placeholder)

        controls = tk.Frame(content, bg=COLORS["window"])
        controls.pack(fill="x", pady=(0, 10))
        self.select_all_button = ttk.Button(
            controls,
            text="Select all visible",
            style="Quiet.TButton",
            command=self.select_all_visible,
        )
        self.select_all_button.pack(side="left")
        self.select_none_button = ttk.Button(
            controls,
            text="Select none",
            style="Quiet.TButton",
            command=self.select_none,
        )
        self.select_none_button.pack(side="left", padx=(7, 0))
        self.selection_label = ttk.Label(
            controls, text="0 selected", style="Secondary.TLabel"
        )
        self.selection_label.pack(side="right", padx=(0, 2))

        list_frame = tk.Frame(
            content,
            bg=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["separator"],
        )
        list_frame.pack(fill="both", expand=True)
        self.artist_list = tk.Listbox(
            list_frame,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            selectbackground=COLORS["green_dim"],
            selectforeground=COLORS["text"],
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=COLORS["surface"],
            highlightcolor=COLORS["green"],
            font=("Segoe UI", 10),
            selectmode=tk.EXTENDED,
            exportselection=False,
            yscrollcommand=self._set_scrollbar,
        )
        self.artist_list.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=7)
        self.scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.artist_list.yview
        )
        self.scrollbar.pack(side="right", fill="y", padx=(0, 5), pady=5)
        self.artist_list.bind("<<ListboxSelect>>", self._on_selection_change)
        self.artist_list.bind("<Motion>", self._on_list_hover)
        self.artist_list.bind("<Leave>", self._on_list_leave)

        self.empty_label = ttk.Label(
            list_frame,
            text="Your followed artists will appear here.",
            style="Muted.TLabel",
            justify="center",
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        footer = tk.Frame(content, bg=COLORS["window"], pady=14)
        footer.pack(fill="x")
        self.status_label = ttk.Label(footer, text="Ready", style="Status.TLabel")
        self.status_label.pack(side="left", pady=5)
        self.unfollow_button = ttk.Button(
            footer,
            text="Unfollow selected",
            style="Primary.TButton",
            command=self.unfollow_selected,
            state="disabled",
        )
        self.unfollow_button.pack(side="right")
        self.operation_progress = ttk.Progressbar(
            footer, mode="determinate", length=115, maximum=100
        )

    @staticmethod
    def center_window(window: tk.Toplevel, width: int, height: int) -> None:
        window.update_idletasks()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def start(self) -> None:
        saved_error = ""
        try:
            self._saved_credentials = self.credential_store.load()
        except CredentialStoreError as error:
            saved_error = str(error)

        credentials, source, prefill_id, prefill_secret = choose_startup_credentials(
            os.environ, self._saved_credentials
        )
        if credentials is None:
            self._dialog = CredentialDialog(
                self,
                client_id=prefill_id,
                client_secret=prefill_secret,
                error_text=saved_error,
                saved_credentials_available=self._saved_credentials is not None,
            )
            return

        self._dialog = ConnectingDialog(self)
        self.begin_authentication(
            credentials,
            self._dialog,
            remember=None,
            startup=True,
        )

    def open_credentials(self) -> None:
        if self._auth_in_progress or self._work_in_progress:
            return
        if isinstance(self._dialog, CredentialDialog):
            return
        storage_error = ""
        try:
            saved = self.credential_store.load()
        except CredentialStoreError as error:
            saved = None
            storage_error = str(error)
        self._saved_credentials = saved
        self._dialog = CredentialDialog(
            self,
            client_id=saved.client_id if saved else "",
            client_secret=saved.client_secret if saved else "",
            error_text=storage_error,
            remember=saved is not None,
            saved_credentials_available=saved is not None,
        )

    def begin_authentication(
        self,
        credentials: SpotifyCredentials,
        dialog: CredentialDialog | ConnectingDialog,
        remember: bool | None,
        startup: bool,
    ) -> None:
        if self._auth_in_progress:
            return
        self._auth_in_progress = True
        self._auth_serial += 1
        serial = self._auth_serial
        self._dialog = dialog
        dialog.set_busy(True) if isinstance(dialog, CredentialDialog) else None
        self.credentials_button.configure(state="disabled")
        if self.client is not None:
            self._set_controls_enabled(False)
        worker = threading.Thread(
            target=self._authentication_worker,
            args=(serial, credentials),
            daemon=True,
        )
        worker.start()
        self._auth_context = (serial, credentials, remember, startup)

    def _authentication_worker(
        self, serial: int, credentials: SpotifyCredentials
    ) -> None:
        try:
            client, artists = connect_and_fetch(credentials)
        except AuthenticationFailure as error:
            self._events.put(("auth_error", serial, str(error)))
        except OSError:
            self._events.put(
                (
                    "auth_error",
                    serial,
                    "Could not access the local Spotify token cache. Check folder "
                    "permissions and try again.",
                )
            )
        except Exception:
            self._events.put(
                (
                    "auth_error",
                    serial,
                    "Spotify connection failed unexpectedly. Check your connection "
                    "and try again.",
                )
            )
        else:
            self._events.put(("auth_success", serial, (client, artists)))

    def cancel_authentication(
        self, dialog: CredentialDialog | ConnectingDialog
    ) -> None:
        if not self._auth_in_progress:
            if dialog is self._dialog:
                self._dialog = None
                if self.client is None:
                    self.shutdown()
            return

        serial, _credentials, _remember, startup = getattr(
            self, "_auth_context", (self._auth_serial, None, None, self.client is None)
        )
        self._auth_cancelled.add(serial)
        if dialog is self._dialog:
            self._dialog = None
        if startup and self.client is None:
            self.shutdown()
        else:
            self.set_status("Connection attempt cancelled.")

    def _process_events(self) -> None:
        while True:
            try:
                event, serial, payload = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event, serial, payload)
        try:
            self.root.after(100, self._process_events)
        except tk.TclError:
            return

    def _handle_event(self, event: str, serial: int, payload: Any) -> None:
        context = getattr(self, "_auth_context", None)
        if event.startswith("auth_"):
            if context is None or context[0] != serial:
                return
            self._auth_in_progress = False
            self.credentials_button.configure(state="normal")
            if serial in self._auth_cancelled:
                self._auth_cancelled.discard(serial)
                self._dialog = None
                if self.client is not None:
                    self._set_controls_enabled(True)
                return

            _serial, credentials, remember, startup = context
            dialog = self._dialog
            if event == "auth_error":
                if self.client is not None:
                    self._set_controls_enabled(True)
                if isinstance(dialog, ConnectingDialog):
                    dialog.close()
                    self._dialog = CredentialDialog(
                        self,
                        client_id=credentials.client_id,
                        client_secret=credentials.client_secret,
                        error_text=payload,
                        remember=(
                            remember is True or self._saved_credentials is not None
                        ),
                        saved_credentials_available=self._saved_credentials is not None,
                    )
                elif isinstance(dialog, CredentialDialog):
                    dialog.show_error(payload)
                elif startup:
                    self._dialog = CredentialDialog(
                        self,
                        client_id=credentials.client_id,
                        client_secret=credentials.client_secret,
                        error_text=payload,
                        remember=(
                            remember is True or self._saved_credentials is not None
                        ),
                        saved_credentials_available=self._saved_credentials is not None,
                    )
                else:
                    self._set_controls_enabled(True)
                    self.set_status(payload)
                return

            if isinstance(dialog, ConnectingDialog):
                dialog.close()
            elif isinstance(dialog, CredentialDialog):
                dialog.close()
            self._dialog = None
            client, artists = payload
            storage_warning = ""
            try:
                if remember is True:
                    self.credential_store.save(credentials)
                    self._saved_credentials = credentials
                elif remember is False and self.credential_store.available:
                    self.credential_store.delete()
                    self._saved_credentials = None
            except CredentialStoreError as error:
                storage_warning = str(error)

            self.client = client
            self.artists = artists
            self.selected_ids.clear()
            self._set_controls_enabled(True)
            self._render_artists()
            self.root.deiconify()
            self.connection_dot.delete("all")
            self.connection_dot.create_oval(
                1, 1, 9, 9, fill=COLORS["green"], outline=""
            )
            self.connection_label.configure(text="Connected")
            if storage_warning:
                self.set_status(f"Connected. {storage_warning}")
                messagebox.showwarning(APP_TITLE, storage_warning, parent=self.root)
            elif startup:
                self.set_status(f"Loaded {len(artists)} followed artists.")
            else:
                self.set_status("Spotify credentials updated.")
            return

        if event == "reload_success":
            self._work_in_progress = False
            self._set_controls_enabled(True)
            self.artists = payload
            self.selected_ids.intersection_update(
                artist["id"] for artist in self.artists
            )
            self._render_artists()
            self.set_status(f"Loaded {len(payload)} followed artists.")
        elif event == "reload_error":
            self._work_in_progress = False
            self._set_controls_enabled(True)
            self.set_status(payload)
        elif event == "unfollow_progress":
            completed, total = payload
            self.operation_progress.configure(
                value=(completed / total * 100) if total else 0
            )
            self.set_status(f"Unfollowing artists… {completed} of {total}")
        elif event == "unfollow_success":
            self._work_in_progress = False
            self._set_controls_enabled(True)
            removed_ids = set(payload)
            self.artists = [
                artist for artist in self.artists if artist["id"] not in removed_ids
            ]
            self.selected_ids.difference_update(removed_ids)
            self.operation_progress.pack_forget()
            self._render_artists()
            self.set_status(f"Unfollowed {len(removed_ids)} artists.")
        elif event == "unfollow_error":
            removed_ids, completed, total, message = payload
            self._work_in_progress = False
            self._set_controls_enabled(True)
            self.artists = [
                artist for artist in self.artists if artist["id"] not in removed_ids
            ]
            self.selected_ids.difference_update(removed_ids)
            self.operation_progress.pack_forget()
            self._render_artists()
            self.set_status(
                f"Unfollow stopped after {completed} of {total} artists. {message}"
            )

    def reload_artists(self) -> None:
        if self.client is None or self._work_in_progress or self._auth_in_progress:
            return
        self._work_in_progress = True
        self._set_controls_enabled(False)
        self.set_status("Loading followed artists…")
        threading.Thread(target=self._reload_worker, daemon=True).start()

    def _reload_worker(self) -> None:
        try:
            artists = fetch_followed_artists(self.client)
        except Exception as error:
            self._events.put(("reload_error", 0, spotify_error_message(error)))
        else:
            self._events.put(("reload_success", 0, artists))

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.reload_button.configure(state=state)
        self.credentials_button.configure(state=state)
        self.select_all_button.configure(state=state)
        self.select_none_button.configure(state=state)
        self.search_entry.configure(state=state)
        self.artist_list.configure(state=state)
        self.unfollow_button.configure(
            state="normal" if enabled and self.selected_ids else "disabled"
        )

    def unfollow_selected(self) -> None:
        if self.client is None or self._work_in_progress:
            return
        selected = self._selected_artists()
        if not selected:
            return
        confirmed = messagebox.askyesno(
            APP_TITLE,
            f"Unfollow {len(selected)} selected artist"
            f"{'s' if len(selected) != 1 else ''}?\n\n"
            "This change takes effect on your Spotify account.",
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return

        ids = [artist["id"] for artist in selected]
        self._work_in_progress = True
        self._set_controls_enabled(False)
        self.operation_progress.configure(value=0)
        self.operation_progress.pack(side="right", padx=(0, 12), pady=6)
        self.set_status(f"Unfollowing 0 of {len(ids)} artists…")
        threading.Thread(
            target=self._unfollow_worker, args=(ids,), daemon=True
        ).start()

    def _unfollow_worker(self, artist_ids: list[str]) -> None:
        completed = 0
        try:
            for start in range(0, len(artist_ids), PAGE_SIZE):
                batch = artist_ids[start : start + PAGE_SIZE]
                self.client.user_unfollow_artists(batch)
                completed += len(batch)
                self._events.put(
                    ("unfollow_progress", 0, (completed, len(artist_ids)))
                )
        except Exception as error:
            self._events.put(
                (
                    "unfollow_error",
                    0,
                    (artist_ids[:completed], completed, len(artist_ids), spotify_error_message(error)),
                )
            )
        else:
            self._events.put(("unfollow_success", 0, artist_ids))

    def select_all_visible(self) -> None:
        if self.artist_list.size():
            self.artist_list.selection_set(0, tk.END)
        self._on_selection_change()

    def select_none(self) -> None:
        self.selected_ids.clear()
        self.artist_list.selection_clear(0, tk.END)
        self._on_selection_change()

    def _on_selection_change(self, _event: tk.Event | None = None) -> None:
        visible_ids = {artist["id"] for artist in self.visible_artists}
        visible_selection = {
            self.visible_artists[index]["id"]
            for index in self.artist_list.curselection()
            if index < len(self.visible_artists)
        }
        self.selected_ids = merge_visible_selection(
            self.selected_ids, visible_ids, visible_selection
        )
        self._update_selection_state()

    def _selected_artists(self) -> list[dict[str, Any]]:
        selected = self.selected_ids
        return [artist for artist in self.artists if artist["id"] in selected]

    def _update_selection_state(self) -> None:
        self.selection_label.configure(text=f"{len(self.selected_ids)} selected")
        selected = bool(self.selected_ids) and not self._work_in_progress
        self.unfollow_button.configure(state="normal" if selected else "disabled")

    def _on_search_change(self, *_args: Any) -> None:
        self._render_artists()

    def _hide_search_placeholder(self, _event: tk.Event | None = None) -> None:
        self.search_placeholder.place_forget()

    def _show_search_placeholder(self, _event: tk.Event | None = None) -> None:
        if not self.search_var.get():
            self.search_placeholder.place(x=12, y=8)

    def _render_artists(self) -> None:
        query = self.search_var.get().strip().casefold()
        self.visible_artists = [
            artist
            for artist in self.artists
            if query in artist.get("name", "").casefold()
        ]
        self.artist_list.delete(0, tk.END)
        for artist in self.visible_artists:
            name = " ".join(str(artist.get("name", "Unknown artist")).split())
            followers = artist.get("followers", {}).get("total")
            label = f"  {name}"
            if isinstance(followers, int):
                label += f"   ·   {followers:,} followers"
            self.artist_list.insert(tk.END, label)
            index = self.artist_list.size() - 1
            self.artist_list.itemconfig(index, background=COLORS["surface"])
            if artist["id"] in self.selected_ids:
                self.artist_list.selection_set(index)
        self.artist_count_label.configure(
            text=f"{len(self.visible_artists)} of {len(self.artists)}"
            if query
            else f"{len(self.artists)}"
        )
        if self.visible_artists:
            self.empty_label.place_forget()
        else:
            empty_message = (
                "No artists match your search."
                if query
                else "Your followed artists will appear here."
            )
            self.empty_label.configure(text=empty_message)
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        self._on_selection_change()

    def _on_list_hover(self, event: tk.Event) -> None:
        index = self.artist_list.nearest(event.y)
        if index >= len(self.visible_artists) or index == self._hovered_index:
            return
        self._clear_hover()
        self._hovered_index = index
        self.artist_list.itemconfig(index, background=COLORS["hover"])

    def _on_list_leave(self, _event: tk.Event | None = None) -> None:
        self._clear_hover()

    def _clear_hover(self) -> None:
        if self._hovered_index is not None:
            if self._hovered_index < self.artist_list.size():
                self.artist_list.itemconfig(
                    self._hovered_index, background=COLORS["surface"]
                )
            self._hovered_index = None

    def _set_scrollbar(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def shutdown(self) -> None:
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = SpotifyArtistManager(root)
    app.start()
    root.mainloop()


if __name__ == "__main__":
    main()
