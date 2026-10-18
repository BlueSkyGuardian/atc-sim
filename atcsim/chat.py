"""Coordination chat: written messages between our sector and the other sectors / adjacent units.

Every message names its sender and its destination, as stations:
* ours: our ATSU and the sectors we control, e.g. "GMAC WS" (the controller's seat);
* the Agadir sectors not controlled in this exercise, e.g. "GMAC WN";
* the adjacent units of the airspace (GMMM, LPPC, GCCC, LECS, MADEIRA for Agadir).
The pseudo-pilots and the instructor play the other stations, as on the telephone. A message may be about an
aircraft (callsign) and answer another one (ref).
"""
from __future__ import annotations

from typing import Dict, List, Optional

MAX_MESSAGES = 2000
MAX_LEN = 300
QUICK = ("APPROVED", "UNABLE", "STANDBY", "ROGER", "AFFIRM", "NEGATIVE")


def _init(w) -> None:
    if not hasattr(w, "chat_msgs"):
        w.chat_msgs = []
        w._chat_seq = 0


def stations(w) -> Dict[str, dict]:
    """{station id: {"name", "kind": own | sector | unit}} for this airspace."""
    from . import cpdlc
    own = cpdlc.own(w)
    asp = w.airspace
    ctl = [s.id for s in asp.sectors if s.id in (asp.controlled or [])] or [s.id for s in asp.sectors]
    out: Dict[str, dict] = {}
    me = f"{own['address']} {'+'.join(ctl)}".strip() if ctl else own["address"]
    out[me] = {"name": f"{own['name']} {'+'.join(ctl)}".strip(), "kind": "own"}
    for s in asp.sectors:
        if s.id not in ctl:
            out[f"{own['address']} {s.id}"] = {"name": f"{own['name']} {s.name.upper()}", "kind": "sector", "freq": s.freq or ""}
    units = ((getattr(asp, "notes", {}) or {}).get("cpdlc") or {}).get("units") or {}
    for u, d in units.items():
        out[u] = {"name": d.get("name") or u, "kind": "unit", "freq": d.get("freq") or ""}
    if len(out) == 1:
        out["ADJ"] = {"name": "ADJACENT SECTOR", "kind": "unit"}
    return out


def own_station(w) -> str:
    return next(k for k, v in stations(w).items() if v["kind"] == "own")


def resolve(w, name: str) -> str:
    """A station id from what was typed: the id, a unit code, or a sector id (WN -> GMAC WN)."""
    st = stations(w)
    n = str(name or "").strip().upper()
    if n in st:
        return n
    for k in st:
        if k.endswith(" " + n) or k.split(" ")[0] == n and st[k]["kind"] != "sector":
            return k
    raise ValueError(f"unknown station {name}: one of {', '.join(st)}")


def send(w, frm: str, to: str, text: str, callsign: Optional[str] = None, ref: Optional[int] = None,
         by: str = "") -> dict:
    _init(w)
    f, t = resolve(w, frm), resolve(w, to)
    if f == t:
        raise ValueError("sender and destination are the same station")
    txt = " ".join(str(text or "").split())[:MAX_LEN]
    if not txt:
        raise ValueError("empty message")
    cs = (callsign or "").strip().upper() or None
    if cs and cs not in w.aircraft:
        raise ValueError(f"unknown callsign {cs}")
    if ref is not None:
        r = next((m for m in w.chat_msgs if m["id"] == int(ref)), None)
        if r is None:
            raise ValueError(f"no message {ref}")
        cs = cs or r.get("callsign")
    from .cpdlc import _utc
    w._chat_seq += 1
    m = {"id": w._chat_seq, "t": round(w.t, 1), "utc": _utc(w), "from": f, "to": t, "text": txt, "callsign": cs,
         "ref": ref, "by": by}
    w.chat_msgs.append(m)
    if len(w.chat_msgs) > MAX_MESSAGES:
        w.chat_msgs = w.chat_msgs[-MAX_MESSAGES:]
    w._event("chat", message_id=m["id"], sender=f, destination=t, callsign=cs,
             text=f"{f} → {t}: " + (f"{cs} " if cs and cs not in txt else "") + txt)
    return m


def messages(w, since: int = 0, station: Optional[str] = None) -> List[dict]:
    _init(w)
    s = resolve(w, station) if station else None
    return [m for m in w.chat_msgs if m["id"] > since and (s is None or s in (m["from"], m["to"]))]
