import os
import sys

import uvicorn

host = os.environ.get("CT_HOST", "127.0.0.1")
if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("CT_PASSWORD"):
    # The database says which wallets are yours; never serve it to a network unprotected.
    sys.exit("Refusing to listen on a non-loopback address without CT_PASSWORD.")
uvicorn.run("tracker.app:app", host=host, port=int(os.environ.get("CT_PORT", "8000")))
