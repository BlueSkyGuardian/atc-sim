"""Start the simulator.  python run.py            -> listens on 0.0.0.0:8000 (whole local network)
                         python run.py --port 9000 --host 127.0.0.1
"""
import argparse
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
    a = ap.parse_args()
    print(f"\n  Radar view : http://{lan_ip()}:{a.port}/")
    print(f"  API docs   : http://{lan_ip()}:{a.port}/docs\n")
    # behind a reverse proxy (Caddy, nginx) the original scheme and client address come from X-Forwarded-*
    uvicorn.run("atcsim.server:app", host=a.host, port=a.port, log_level="warning", proxy_headers=True,
                forwarded_allow_ips="*", ws_max_size=1 << 20)
