import os
import secrets
import sys
from pathlib import Path

import uvicorn

from tracker import sync


def password_from_file():
    """Without CT_PASSWORD, the one kept in CT_PASSWORD_FILE, made on first start and printed
    once, so that a container starts protected with nothing to set up."""
    path = os.environ.get("CT_PASSWORD_FILE")
    if os.environ.get("CT_PASSWORD") or not path:
        return
    file = Path(path)
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(secrets.token_urlsafe(18) + "\n")
        print(f"Generated a password, kept in {file}: {file.read_text().strip()}", flush=True)
    os.environ["CT_PASSWORD"] = file.read_text().strip()


if __name__ == "__main__":
    password_from_file()
    host = os.environ.get("CT_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("CT_PASSWORD"):
        # The database says which wallets are yours; never serve it to a network unprotected.
        sys.exit("Refusing to listen on a non-loopback address without CT_PASSWORD.")
    # Wallets not read in full keep filling in between syncs (the app is imported by uvicorn,
    # in this process; tests that import it start no thread).
    sync.every(sync.FILL_EVERY, sync.fill_in)
    uvicorn.run("tracker.app:app", host=host, port=int(os.environ.get("CT_PORT", "8000")))
