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
| `ATC_SMTP_HOST` | the mail server for the notification e-mails (invitations to exercises and debriefs, shared scenarios, evaluations, new accounts, role changes). Not set: the e-mails are written to the data folder's `outbox` instead of being sent |
| `ATC_SMTP_PORT` | default 587 |
| `ATC_SMTP_SECURITY` | `starttls` (default, port 587), `ssl` (port 465) or `none` |
| `ATC_SMTP_USER`, `ATC_SMTP_PASSWORD` | the mail account (for Gmail: an [app password](https://support.google.com/accounts/answer/185833), not the account's password) |
| `ATC_SMTP_FROM` | the sender address (default: `ATC_SMTP_USER`) |
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

### E-mail and notifications

Everyone gets a notification in the bell at the top of the pages, and an e-mail, when they are invited to an
exercise or a debrief, a scenario is shared with them, their work is evaluated, their account is created in
advance or their role changes. The links in the e-mails use `ATC_PUBLIC_URL`. Each user can turn the e-mails off in
their profile (the bell stays). The administrator's *Users* page shows how mail goes out, the last e-mails and their
delivery, and sends a test e-mail. Mail is sent in the background: a mail server that is down never fails an
invitation (the failure shows on the *Users* page).

### Aviation news and the daily newsletter

`/news` is public (no sign-in): the latest aviation stories, air traffic control first. The server reads RSS feeds
every hour (aviation sites and Google News searches on air traffic control, air navigation technology and Morocco;
the administrator edits the list on the *Users* page) and keeps the headline, the feed's short excerpt and the link -
never the article. Every morning (07:00 Casablanca time by default) it builds the newsletter from the last day's
stories and mails it to the subscribers; the issue is then on the news page too.

* **Who gets it**: every account, by default (new accounts at their first sign-in or when the administrator adds
  them; the accounts that existed before, once). Each person unsubscribes from their **Profile** (asked to confirm)
  or from the link in every newsletter (a page with a button, and the one-click `List-Unsubscribe` header); their
  choice stands - signing in again does not subscribe them back. Anyone without an account subscribes on the news
  page and confirms by a link sent to the address.
* **The writing**: with a language model, the stories are picked, sorted into sections and summarized in one or two
  sentences (from the headline and excerpt only), with a title and an introduction. Add the model in the **AI lab**
  as an OpenAI-compatible endpoint, then choose it in the newsletter settings. For example DeepSeek: base URL
  `https://api.deepseek.com/v1`, model `deepseek-chat`, its API key; Google Gemini: base URL
  `https://generativelanguage.googleapis.com/v1beta/openai`, model `gemini-2.5-flash-lite`. One issue is a few
  thousand tokens: well under a cent a day with these. Without a model the stories are picked by keywords, with the
  feeds' own excerpts.
* **Volume**: a Gmail account sends about 500 e-mails a day (the setting *at most … e-mails a day*, 450 by default);
  beyond that, use a mail service's SMTP relay (Brevo, Mailgun, Amazon SES...) with the same `ATC_SMTP_*` settings.
* **The newsroom's own articles**: 3 to 5 a day (Morocco first, then Europe, then international), each with a chart
  or a map, written every morning by a scheduled agent (Claude) following `tools/newsdesk/INSTRUCTIONS.md` and pushed
  to the `news` branch of the repository. The server reads `articles/index.json` there every hour
  (`ATC_ARTICLES_URL`, default `https://raw.githubusercontent.com/BlueSkyGuardian/atc-sim/news/articles`), checks
  each article again (no HTML, https links only, figures that are plain SVG and PNG) and publishes it on `/news/a/<slug>`
  and at the top of the next newsletter. The *Users* page shows the newsroom's last reading.
* `ATC_NEWS=off` stops the hourly reading and the newsletter (a test machine without internet).

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

## Conflict solver

The solver (Solver panel on the solo radar, *Built-in solver* in the AI lab, solver hints for models) needs no
setup. Its close-range Hamilton-Jacobi tables (separation minima 5 and 7 NM, five speed ratios, about 1 MB each) are
built ahead and shipped in `atcsim/solve/tables`. A table is used only if it was built for the present parameters;
a missing or outdated one (another minimum, a changed horizon or grid) is built in the background the first time it
is needed, about a minute each, into `DATA_DIR/hj`. After changing the reachability parameters in
`atcsim/solve/hj.py`, rebuild the shipped tables and commit them: `python -m atcsim.solve.hj --ship 5 7`.
