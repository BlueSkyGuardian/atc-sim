"""Start the simulator.  python run.py            -> listens on 0.0.0.0:8000 (whole local network)
                         python run.py --port 9000 --host 127.0.0.1
                         python run.py --dev-login   -> test sign-in with any e-mail (local test machine only)
"""
import argparse
import os
import socket

import uvicorn


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dev-login", action="store_true", help="password-less test sign-in (any e-mail): local tests only")
    a = ap.parse_args()
    if a.dev_login:
        os.environ["ATC_DEV_LOGIN"] = "1"
        print("\n  !! test sign-in on: anyone can sign in as any e-mail. Local tests only.")
    print(f"\n  Radar view : http://{lan_ip()}:{a.port}/")
    print(f"  API docs   : http://{lan_ip()}:{a.port}/docs\n")
    # behind a reverse proxy (Caddy, nginx) the original scheme and client address come from X-Forwarded-*
    uvicorn.run("atcsim.server:app", host=a.host, port=a.port, log_level="warning", proxy_headers=True,
                forwarded_allow_ips="*", ws_max_size=1 << 20)
