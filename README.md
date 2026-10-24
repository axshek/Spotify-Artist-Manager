# Spotify Artist Manager

A desktop Tkinter app for viewing and unfollowing Spotify artists.

## Requirements

- Python 3.10+
- A Spotify Developer application

## Setup

```powershell
python -m pip install -r requirements.txt
python spotify_unfollow_gui.py
```

When prompted, enter your Spotify Client ID and Client Secret. Add this redirect URI to your Spotify Developer application:

```text
http://127.0.0.1:8888/callback
```

Credentials are stored locally using the Windows Credential Manager when available; they are not included in this repository.

