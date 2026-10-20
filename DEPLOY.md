# Putting the simulator on the internet

The connected tool (instructors, controllers, pseudo-pilots, radio and telephone, recordings) runs as one
server behind HTTPS. HTTPS is not optional: browsers only give a page the microphone when it is served
securely (or from `localhost`).

What you need: a small Linux server (2 vCPU / 4 GB is enough for a few simultaneous exercises; recording
rendering uses one core for a minute or two after each exercise), a domain name, Docker.

## 1. Domain and server

1. Create a DNS `A` record, e.g. `atc.example.com`, pointing to the server's public IP address.
2. Open ports 80 and 443 in the server's firewall (80 is used once by Let's Encrypt to issue the certificate).
3. Install Docker (`curl -fsSL https://get.docker.com | sh`).

## 2. Google sign-in (the OAuth client)

In the [Google Cloud console](https://console.cloud.google.com/):

1. Create a project (or pick one), e.g. *ATC simulator*.
2. **APIs & Services → OAuth consent screen**: user type *External*, app name *ATC simulator*, your support
   e-mail, scopes `openid`, `email`, `profile` (the defaults). While the app is in *Testing* mode, only the Gmail
   addresses you add as *test users* can sign in (up to 100); click *Publish app* to let any Google account in
   (the simulator still only lets them do what their role allows, and new accounts start as controllers with
   no exercise).
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   * Application type: **Web application**
   * Authorised JavaScript origins: `https://atc.example.com`
   * Authorised redirect URIs: `https://atc.example.com/auth/callback`
4. Copy the *Client ID* and *Client secret*.

## 3. Configure and start

```bash
git clone <this repository> atc-sim        # or copy the folder to the server
cd atc-sim/deploy
cp env.example .env
nano .env                                    # domain, public URL, Google client, your Gmail as admin
docker compose up -d --build
```

Open `https://atc.example.com`, sign in with the Gmail address listed in `ATC_ADMIN_EMAILS`: you are the
administrator. Complete your profile (first name, last name, licence number).

## 4. Accounts and roles

* Everybody signs in with their Google account and fills in first name, last name and licence number.
* A new account is a **controller**. The administrator changes roles on the **Users** page
  (controller, pseudo-pilot, instructor, administrator) and can disable accounts. Accounts can also be
  created in advance there (the person then signs in with that Gmail address).
* Instructors invite people to an exercise from the user list (they find them by name or licence number), or
  send them the controller / pseudo-pilot link of the exercise.

## 5. Settings (`.env`)

| Variable | Meaning |
|---|---|
| `ATC_DOMAIN` | the domain Caddy gets a certificate for |
| `ATC_PUBLIC_URL` | `https://` + domain; used for the Google redirect and the invitation links |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | the OAuth client |
| `ATC_ADMIN_EMAILS` | Gmail addresses that are always administrators (comma separated) |
| `ATC_SECRET_KEY` | optional: signs the session cookies (default: generated once into the data volume) |
| `ATC_API_KEY` | optional: scripts and agents send it as `X-API-Key` and get full rights |
| `ATC_AUTH` | `off` = the old open single-user mode (only on your own computer, never on the internet) |
| `ATC_PRED_INTERVAL_S` | minimum seconds between two runs of the 10-minute conflict look-ahead of an exercise (default 8; it also waits 3× its own computing time). The short-term conflict alert (3 min) is a separate fast check, every second |
| `ATC_PRED_WORKERS` | look-aheads computed at the same time over the whole server (default 1; raise it on a server with more cores) |
| `ATC_DEV_LOGIN` | `1` = password-less test login form (anyone signs in as any e-mail). Off unless set; for a local test machine only, never online |

## Oracle Cloud (Always Free) notes

* Give the instance a public IP (instance → Networking → the VNIC → IPv4 addresses → Edit → Ephemeral public IP),
  and add ingress rules TCP 80 and 443 from `0.0.0.0/0` to the subnet's security list.
* Oracle Linux (user `opc`) has its own firewall as well:
  `sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload`
  (Ubuntu images: `iptables` rules + `netfilter-persistent save`).
* Docker on Oracle Linux comes from Docker's RHEL repository:
  `sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo` then
  `sudo dnf install -y --allowerasing docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`,
  `sudo systemctl enable --now docker`, `sudo usermod -aG docker opc`.
* 1 OCPU / 6 GB runs a couple of exercises: the look-ahead is shared and throttled (see `ATC_PRED_*`), and the
  recordings are rendered one at a time at low priority after the exercises.

## 6. Data, backups, updates

Everything lives in the `atc-data` Docker volume: `atcsim.db` (accounts, scenario sharing, exercises),
`recordings/<exercise>/exercise.mp4` and the raw material it was made from, `debriefs/<exercise>/` (the whole run of
each ended exercise for its debrief on the simulator, and the alternatives tried in it: a few MB per hour flown), and
`ai/` (the AI lab's runs, their decisions and the exported datasets) and `secret.key` (it also encrypts the
AI lab's API keys: keep it, or the keys must be entered again).
Scenarios saved with the designer are in `scenarios/` on the host.

```bash
docker compose exec atcsim sqlite3 /data/atcsim.db ".backup /data/backup.db"   # or copy the volume
docker compose pull && docker compose up -d --build                            # update
```

A recording takes about 1 MB per minute of exercise. Archive or delete old ones from the home page.

## 7. Agents and scripts

With accounts on, HTTP clients (such as `examples/baseline_agent.py`) need the API key:

```bash
ATC_API_KEY=... python examples/baseline_agent.py --server https://atc.example.com --scenario s10_busy_mixed
```

## Local test without Google

```bash
python run.py --port 8000 --dev-login   # the sign-in page offers a test login (any e-mail): local tests only
```

The first account created that way is administrator (unless `ATC_ADMIN_EMAILS` is set). The microphone
works on `http://localhost`; from other computers on the network it needs HTTPS, e.g. a quick local
certificate with Caddy: `caddy reverse-proxy --from <your-ip>.nip.io --to localhost:8000`.

## Updating during an exercise

Running exercises are saved in the data volume (`/data/live/<exercise>`) every 10 simulated seconds. A
`docker compose up -d --build` (or a crash) does not lose them: when the server starts again they are back, paused
at their last save, and the instructor presses Run. The save is removed when the exercise ends.


## AI lab

The AI lab calls the models' APIs from the server (outbound HTTPS to `api.runpod.ai`, or your pod's proxy URL).
`ATC_AI_PARALLEL` (default 2) sets how many headless runs go at the same time; each runs in its own process and uses
about one CPU core while the model is answering quickly. A dataset's *RunPod link* is
`<ATC_PUBLIC_URL>/ai-data/<id>/<token>.jsonl`: set `ATC_PUBLIC_URL` so the link is reachable from the pod.
