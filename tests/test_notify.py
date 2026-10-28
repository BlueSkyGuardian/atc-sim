"""Notifications and e-mail: invitations to exercises and debriefs, shared scenarios, new accounts and role changes
reach the bell and the mailbox; the bell's API (unread, mark read); the e-mail opt-out; the outbox without a mail
server, real SMTP with one (a tiny local SMTP server). Run:  python -m tests.test_notify   (throw-away data folder)."""
import os
import socket
import sys
import tempfile
import threading
import time

os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-notify-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"
os.environ.pop("ATC_SMTP_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app  # noqa: E402
from atcsim.platform import db, notify  # noqa: E402


def client(email):
    c = TestClient(app)
    r = c.post("/auth/dev", json={"email": email})
    assert r.status_code == 200, r.text
    return c


def ok(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        sys.exit(1)


def outbox(to=None):
    d = notify.OUTBOX
    names = sorted(os.listdir(d)) if os.path.isdir(d) else []
    return [n for n in names if to is None or to.replace("@", "_at_") in n]


def bell(c):
    return c.get("/api/notifications").json()


boss, ins, ins2, atc, pil = (client(e) for e in ("boss@gmail.com", "ins@gmail.com", "ins2@gmail.com", "atc@gmail.com", "pil@gmail.com"))
for c, (fn, ln) in zip((boss, ins, ins2, atc, pil), (("Big", "Boss"), ("Ina", "Structor"), ("Ian", "Two"), ("Alaa", "Hamza"), ("Pierre", "Lote"))):
    c.put("/api/me", json={"first_name": fn, "last_name": ln, "licence": "X-1"})
uid = {u["email"]: u["id"] for u in boss.get("/api/users").json()}
notify.flush()                                            # the welcome of each first sign-in: read, and out of the way
for c in (boss, ins, ins2, atc, pil):
    c.post("/api/notifications/read", json={})
ok(len(outbox("atc@gmail.com")) == 1, "a welcome e-mail at the first sign-in")
for f in outbox():
    os.remove(os.path.join(notify.OUTBOX, f))

# ---------------------------------------------------------------- roles, accounts made in advance
boss.patch(f"/api/users/{uid['ins@gmail.com']}", json={"role": "instructor"})
boss.patch(f"/api/users/{uid['ins2@gmail.com']}", json={"role": "instructor"})
boss.patch(f"/api/users/{uid['pil@gmail.com']}", json={"role": "pilot"})
notify.flush()
b = bell(ins)
ok(b["unread"] == 1 and b["items"][0]["kind"] == "role" and "instructor" in b["items"][0]["title"],
   f"role change -> the bell: {b['items'][0]['title'] if b['items'] else b}")
ok(b["items"][0]["actor"] == "Big Boss", "with who did it")
ok(len(outbox("ins@gmail.com")) == 1, "and an e-mail (no mail server: written to the outbox)")
r = boss.post("/api/users", json={"email": "newbie@gmail.com", "role": "atc"})
notify.flush()
ok(r.status_code == 200 and len(outbox("newbie@gmail.com")) == 1, "an account added in advance is told by e-mail")
eml = open(os.path.join(notify.OUTBOX, outbox("newbie@gmail.com")[0]), encoding="utf-8", errors="replace").read()
ok("Sign in with your Google account" in eml and "/login" in eml and "Subject: You have an account" in eml,
   "the e-mail says how to sign in, with the link")
ok(bell(boss)["unread"] == 0, "nobody is notified of what they did themselves")

# ---------------------------------------------------------------- exercises
r = ins.post("/api/exercises", json={"scenario": "s01_basic_crossing", "name": "Night shift"})
ok(r.status_code == 200, f"exercise created ({r.status_code})")
eid = r.json()["id"]
ins.post(f"/api/exercises/{eid}/members", json={"user_id": uid["atc@gmail.com"], "seat": "atc"})
ins.post(f"/api/exercises/{eid}/members", json={"user_id": uid["pil@gmail.com"], "seat": "pilot"})
notify.flush()
b = bell(atc)
ok(b["unread"] == 1 and b["items"][0]["kind"] == "exercise_invite" and "Night shift" in b["items"][0]["title"]
   and "controller" in b["items"][0]["title"] and b["items"][0]["link"] == f"/x/{eid}",
   f"invited to an exercise -> the bell: {b['items'][0]['title']}")
ok("pseudo-pilot" in bell(pil)["items"][0]["title"], "the pseudo-pilot too, with their seat")
mail = open(os.path.join(notify.OUTBOX, outbox("atc@gmail.com")[0]), encoding="utf-8", errors="replace").read()
ok(f"/x/{eid}" in mail and "Hello Alaa" in mail, "the e-mail has the link to the exercise and the person's name")
ins.post(f"/api/exercises/{eid}/members", json={"user_id": uid["atc@gmail.com"], "seat": "atc"})
notify.flush()
ok(bell(atc)["unread"] == 1, "invited again to the same seat: no second notification")

# ---------------------------------------------------------------- the e-mail opt-out
ok(atc.get("/api/me/notify").json()["email_notify"] is True, "e-mails are on by default")
atc.put("/api/me/notify", json={"email_notify": False})
n_before = len(outbox("atc@gmail.com"))
r = ins.post("/api/exercises", json={"scenario": "s01_basic_crossing", "name": "Day shift"})
eid2 = r.json()["id"]
ins.post(f"/api/exercises/{eid2}/members", json={"user_id": uid["atc@gmail.com"], "seat": "atc"})
notify.flush()
ok(bell(atc)["unread"] == 2 and len(outbox("atc@gmail.com")) == n_before, "e-mails off: the bell only")
atc.put("/api/me/notify", json={"email_notify": True})

# ---------------------------------------------------------------- scenarios shared
name = f"zz_notify_{int(time.time())}"
r = ins.post("/api/scenarios/s01_basic_crossing/copy", json={"new_name": name})
ok(r.status_code == 200, f"scenario copied ({r.status_code} {r.text[:80]})")
ins.post(f"/api/scenarios/{name}/shares", json={"user_id": uid["ins2@gmail.com"], "perm": "read"})
notify.flush()
b = bell(ins2)
it = [x for x in b["items"] if x["kind"] == "scenario_share"]
ok(it and name in it[0]["title"] and it[0]["link"] == f"/designer?scenario={name}", "a shared scenario -> the bell, link to the designer")
ins.post(f"/api/scenarios/{name}/shares", json={"user_id": uid["ins2@gmail.com"], "perm": "write"})
notify.flush()
ok(len([x for x in bell(ins2)["items"] if x["kind"] == "scenario_share"]) == 2, "given the right to edit: told again")

# ---------------------------------------------------------------- debriefs
ins.post(f"/api/exercises/{eid}/end")
r = ins.post(f"/api/debriefs/{eid}/invite", json={"user_ids": [uid["ins2@gmail.com"]]})
notify.flush()
it = [x for x in bell(ins2)["items"] if x["kind"] == "debrief_invite"]
ok(r.status_code == 200 and it and it[0]["link"] == f"/debrief/{eid}", f"invited to a debrief -> the bell ({r.status_code})")

# ---------------------------------------------------------------- the bell: read
b = bell(atc)
first = b["items"][0]["id"]
b2 = atc.post("/api/notifications/read", json={"ids": [first]}).json()
ok(b2["unread"] == b["unread"] - 1 and b2["items"][0]["read"], "opening one marks it read")
ok(atc.post("/api/notifications/read", json={"ids": [bell(ins2)["items"][0]["id"]]}).json()["unread"] == b2["unread"],
   "nobody marks someone else's read")
ok(atc.post("/api/notifications/read", json={}).json()["unread"] == 0, "mark all read")
ok(TestClient(app).get("/api/notifications").status_code == 401, "signed out: no notifications")

# ---------------------------------------------------------------- admin: mail status, test e-mail
m = boss.get("/api/admin/mail").json()
ok(m["mode"] == "outbox" and m["recent"] and all(r_["emailed"] in ("outbox", "off") for r_ in m["recent"]),
   "admin: no mail server -> outbox, each e-mail's delivery listed")
ok(ins.get("/api/admin/mail").status_code == 403, "admins only")
r = boss.post("/api/admin/test-email", json={})
ok(r.status_code == 200 and r.json()["status"] == "outbox", "test e-mail")

# ---------------------------------------------------------------- real SMTP (a local server that keeps what it gets)
got = []


def smtp_server(sock):
    conn, _ = sock.accept()
    f = conn.makefile("rwb")

    def say(s):
        f.write((s + "\r\n").encode())
        f.flush()
    say("220 test ESMTP")
    data = False
    buf = []
    while True:
        line = f.readline()
        if not line:
            break
        t = line.decode(errors="replace").rstrip("\r\n")
        if data:
            if t == ".":
                data = False
                got.append("\n".join(buf))
                say("250 queued")
            else:
                buf.append(t)
            continue
        cmd = t.split(" ")[0].upper()
        if cmd in ("EHLO", "HELO"):
            say("250 test")
        elif cmd == "DATA":
            data = True
            say("354 go")
        elif cmd == "QUIT":
            say("221 bye")
            break
        else:
            say("250 ok")
    conn.close()


srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
th = threading.Thread(target=smtp_server, args=(srv,), daemon=True)
th.start()
notify.SMTP_HOST, notify.SMTP_PORT, notify.SMTP_SECURITY, notify.SMTP_FROM = "127.0.0.1", srv.getsockname()[1], "none", "sim@example.com"
ins.post(f"/api/exercises/{eid2}/members", json={"user_id": uid["pil@gmail.com"], "seat": "pilot"})
notify.flush()
th.join(5)
ok(got and "To: pil@gmail.com" in got[0] and f"/x/{eid2}" in got[0], "with ATC_SMTP_HOST the e-mail goes to the mail server")
row = db.q1("SELECT emailed FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid["pil@gmail.com"],))
ok(row["emailed"] == "sent", "and is marked sent")
notify.SMTP_HOST, notify.SMTP_PORT = "127.0.0.1", 9          # nothing listens there: the mail fails, not the invite
r = ins.post(f"/api/exercises/{eid2}/members", json={"user_id": uid["ins2@gmail.com"], "seat": "instructor"})
notify.flush()
row = db.q1("SELECT emailed FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid["ins2@gmail.com"],))
ok(r.status_code == 200 and str(row["emailed"]).startswith("failed"), f"mail server down: the invitation still works, the failure is recorded ({row['emailed'][:40]})")
notify.SMTP_HOST = ""
ins.delete(f"/api/scenarios/{name}")                      # the copy is a file in the scenarios folder: remove it
ok(not os.path.exists(os.path.join("scenarios", name + ".json")), "test scenario removed")
print("notification checks passed")
