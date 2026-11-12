#!/usr/bin/env python3
"""
Generates the animated, self-hosted SVG assets for the profile README.

  * dark + light variant of every asset (README picks one with <picture>)
  * terminal.svg is LIVE: each EknathaLabs URL is probed, repo age/commit
    activity is read from the GitHub API
  * pods.svg turns the contribution calendar into a field of pods

Pure standard library. Runs in GitHub Actions (see .github/workflows/update.yml)
or locally:  python scripts/generate.py --out assets [--offline]

Network failures never blank the profile: the last good state is kept in
data/state.json and reused.
"""
import argparse, datetime as dt, json, math, os, random, re, sys, urllib.error, urllib.request

SANS = "'Segoe UI','Inter','Helvetica Neue',Arial,sans-serif"
MONO = "'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace"
TELUGU = "'Noto Sans Telugu','Nirmala UI',Gautami,'Telugu MN',sans-serif"

DARK = dict(
    name="dark", bg1="#060a14", bg2="#101b33", grid="#ffffff", grid_op=".045", star="#cbd5e1",
    glow="#38bdf8", glow_op=".18", t1="#67e8f9", t2="#a78bfa",
    text="#e2e8f0", muted="#94a3b8", dim="#64748b", faint="#475569",
    card="#0a0f1c", card_stroke="#1e293b", bar="#111a2e",
    node="#0b1426", node_stroke="#1e3a5f", link="#27456b",
    cp="#10223f", cp_stroke="#38bdf8", cp_text="#93c5fd",
    pill="#334155", pill_text="#cbd5e1",
    green="#22c55e", red="#ef4444", amber="#fbbf24", cyan="#38bdf8",
    pg="#4ade80", blue="#60a5fa", amber_text="#fbbf24",
    track="#1e3a5f", dot_bg="#0a0f1c", dot_stroke="#334155",
    empty="#1b2538", ramp=["#14532d", "#15803d", "#22c55e", "#86efac"],
)
LIGHT = dict(
    name="light", bg1="#f8fafc", bg2="#e6edfb", grid="#0f172a", grid_op=".05", star="#64748b",
    glow="#38bdf8", glow_op=".22", t1="#0369a1", t2="#6d28d9",
    text="#0f172a", muted="#475569", dim="#64748b", faint="#94a3b8",
    card="#ffffff", card_stroke="#cbd5e1", bar="#eef2f7",
    node="#ffffff", node_stroke="#b6cbe6", link="#93b4d8",
    cp="#e0f2fe", cp_stroke="#0284c7", cp_text="#075985",
    pill="#cbd5e1", pill_text="#334155",
    green="#16a34a", red="#dc2626", amber="#d97706", cyan="#0284c7",
    pg="#15803d", blue="#2563eb", amber_text="#b45309",
    track="#cbd5e1", dot_bg="#ffffff", dot_stroke="#94a3b8",
    empty="#dbe3ee", ramp=["#bbf7d0", "#4ade80", "#16a34a", "#166534"],
)

REDUCED_MOTION = (
    "@media (prefers-reduced-motion: reduce){*{animation:none!important}.s1{opacity:1!important}}"
)


# ================================================================ DATA
def http(url, headers=None, data=None, timeout=12):
    h = {"User-Agent": "profile-readme-bot"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return None, b""


def gh_headers(token):
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def site_status(url):
    """Running = answers 2xx/3xx, Pending = 404/410 (not deployed), Error = 5xx, None = unreachable."""
    for _ in range(2):
        code, _b = http(url, timeout=12)
        if code is not None:
            break
    if code is None:
        return None
    if 200 <= code < 400:
        return "Running"
    if code in (404, 410):
        return "Pending"
    return "Error"


def fmt_age(iso):
    try:
        d = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
        return f"{max(d, 0)}d"
    except Exception:
        return "-"


def repo_stats(repo, token):
    code, body = http(f"https://api.github.com/repos/{repo}", gh_headers(token))
    if code != 200:
        return None, None
    age = fmt_age(json.loads(body).get("created_at", ""))
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    code, body = http(f"https://api.github.com/repos/{repo}/commits?since={since}&per_page=100", gh_headers(token))
    commits = len(json.loads(body)) if code == 200 else None
    return age, commits


def collect_tools(cfg, state, token, online):
    prev_all = state.get("tools", {})
    out = []
    for t in cfg["tools"]:
        prev = prev_all.get(t["name"], {})
        rec = {"name": t["name"], "purpose": t["purpose"],
               "status": prev.get("status", t.get("default", "Pending")),
               "age": prev.get("age", "-"), "restarts": prev.get("restarts", "-")}
        if online:
            s = site_status(t["url"])
            if s is not None:               # unreachable (None) keeps the last known status
                rec["status"] = s
            age, commits = repo_stats(t["repo"], token)
            if age is not None:
                rec["age"] = age
            if commits is not None:
                rec["restarts"] = str(commits)
        out.append(rec)
    return out


def contribs_graphql(user, token):
    if not token:
        return None
    q = ("query($l:String!){user(login:$l){contributionsCollection{contributionCalendar{"
         "weeks{contributionDays{date contributionCount}}}}}}")
    payload = json.dumps({"query": q, "variables": {"l": user}}).encode()
    code, body = http("https://api.github.com/graphql", {**gh_headers(token), "Content-Type": "application/json"}, payload, 20)
    if code != 200:
        return None
    try:
        weeks = json.loads(body)["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        return [[d["date"], d["contributionCount"]] for w in weeks for d in w["contributionDays"]]
    except Exception:
        return None


def contribs_scrape(user):
    code, body = http(f"https://github.com/users/{user}/contributions", timeout=20)
    if code != 200:
        return None
    return parse_contrib_html(body.decode("utf-8", "ignore"))


def parse_contrib_html(html):
    tips = {}
    for m in re.finditer(r'<tool-tip[^>]*for="([^"]+)"[^>]*>([^<]*)</tool-tip>', html):
        n = re.match(r"\s*(\d+)\s+contribution", m.group(2))
        tips[m.group(1)] = int(n.group(1)) if n else 0
    days = []
    for m in re.finditer(r"<td\b[^>]*>", html):
        tag = m.group(0)
        d = re.search(r'data-date="(\d{4}-\d\d-\d\d)"', tag)
        if not d:
            continue
        i = re.search(r'\bid="([^"]+)"', tag)
        lv = re.search(r'data-level="(\d)"', tag)
        count = tips.get(i.group(1)) if i else None
        if count is None:
            count = int(lv.group(1)) if lv else 0
        days.append([d.group(1), count])
    return days or None


def fetch_contribs(user, token):
    return contribs_graphql(user, token) or contribs_scrape(user)


def weeks_from_days(days):
    """flat [[date,count]] -> list of weeks, each a 7-slot list (Sun..Sat) of None | (date,count)."""
    ds = sorted({d[0]: d[1] for d in days}.items())[-371:]
    weeks, cur = [], [None] * 7
    for date, c in ds:
        wd = (dt.date.fromisoformat(date).weekday() + 1) % 7
        if wd == 0 and any(cur):
            weeks.append(cur)
            cur = [None] * 7
        cur[wd] = (date, c)
    if any(cur):
        weeks.append(cur)
    return weeks


def streaks(days):
    ds = sorted(days)
    longest = run = 0
    for _d, c in ds:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    cur, i = 0, len(ds) - 1
    if i >= 0 and ds[i][1] == 0:
        i -= 1                               # today may not have happened yet
    while i >= 0 and ds[i][1] > 0:
        cur += 1
        i -= 1
    return cur, longest


def sample_days():
    random.seed(11)
    start = dt.date.today() - dt.timedelta(days=364)
    out = []
    for i in range(365):
        d = start + dt.timedelta(days=i)
        busy = 0.55 + 0.25 * math.sin(i / 38)
        out.append([d.isoformat(), 0 if random.random() > busy else random.choice([1, 2, 3, 5, 8, 12])])
    return out


# ============================================================== SVG: HERO
def hero(P):
    random.seed(27)
    stars = "".join(
        f'<circle cx="{random.randint(20,980)}" cy="{random.randint(14,326)}" r="{random.choice([0.8,1,1.3])}" '
        f'fill="{P["star"]}" class="tw" style="animation-delay:{random.uniform(0,5):.1f}s"/>'
        for _ in range(26)
    )
    names = ["node-a", "node-b", "node-c"]
    node_svg, pod_svg = "", ""
    for ni, nx in enumerate([590, 715, 840]):
        node_svg += (
            f'<rect x="{nx}" y="140" width="110" height="110" rx="10" fill="{P["node"]}" stroke="{P["node_stroke"]}"/>'
            f'<text x="{nx+55}" y="158" text-anchor="middle" font-family="{MONO}" font-size="10" fill="{P["dim"]}">{names[ni]}</text>'
        )
        for row, cy in enumerate([188, 222]):
            for col in range(3):
                if ni == 1 and row == 0 and col == 1:
                    continue
                pod_svg += (f'<circle cx="{nx+25+col*30}" cy="{cy}" r="8" fill="{P["green"]}" class="pod" '
                            f'style="animation-delay:{random.uniform(0,3):.1f}s"/>')
    victim = ('<circle cx="770" cy="188" r="8" class="ring"/><circle cx="770" cy="188" r="8" class="victim"/>'
              '<circle cx="770" cy="188" r="8" class="reborn"/>')
    links = packets = ""
    for i, tx in enumerate([645, 770, 895]):
        links += f'<line x1="770" y1="78" x2="{tx}" y2="140" stroke="{P["link"]}" stroke-width="1.5" stroke-dasharray="4 5" class="flow"/>'
        packets += (
            f'<circle r="3.2" fill="{P["cyan"]}"><animateMotion dur="2.6s" begin="{i*0.8}s" repeatCount="indefinite" path="M770,78 L{tx},140"/>'
            f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.1;.85;1" dur="2.6s" begin="{i*0.8}s" repeatCount="indefinite"/></circle>'
        )
    pills, px = "", 48
    for label in ["Kubernetes", "OpenStack", "Terraform", "AWS", "Azure", "Linux"]:
        w = int(len(label) * 7.4 + 22)
        pills += (f'<rect x="{px}" y="262" width="{w}" height="26" rx="13" fill="none" stroke="{P["pill"]}"/>'
                  f'<text x="{px+w/2}" y="279.5" text-anchor="middle" font-family="{MONO}" font-size="12" fill="{P["pill_text"]}">{label}</text>')
        px += w + 8
    g, r, a = P["green"], P["red"], P["amber"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 340" width="1000" height="340" role="img" aria-labelledby="t d">
<title id="t">Eknatha Reddy Puli — Platform Engineer and SRE</title>
<desc id="d">Animated header: a small Kubernetes cluster where chaos kills a pod and the cluster self-heals.</desc>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{P["bg1"]}"/><stop offset="1" stop-color="{P["bg2"]}"/></linearGradient>
  <linearGradient id="ttl" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{P["t1"]}"/><stop offset="1" stop-color="{P["t2"]}"/></linearGradient>
  <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="{P["glow"]}" stop-opacity="{P["glow_op"]}"/><stop offset="1" stop-color="{P["glow"]}" stop-opacity="0"/></radialGradient>
  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="{P["grid"]}" stroke-opacity="{P["grid_op"]}"/></pattern>
  <clipPath id="reveal"><rect x="40" y="70" height="70" width="0"><animate attributeName="width" from="0" to="600" dur="1.5s" fill="freeze" calcMode="spline" keySplines=".2 .8 .2 1" keyTimes="0;1"/></rect></clipPath>
  <style>
    .tw{{animation:tw 5s ease-in-out infinite}} @keyframes tw{{0%,100%{{opacity:.15}}50%{{opacity:.9}}}}
    .flow{{animation:dash 1.2s linear infinite}} @keyframes dash{{to{{stroke-dashoffset:-18}}}}
    .pod{{animation:breathe 3s ease-in-out infinite}} @keyframes breathe{{0%,100%{{opacity:.7}}50%{{opacity:1}}}}
    .victim,.reborn,.ring{{transform-box:fill-box;transform-origin:center}}
    .victim{{fill:{g};animation:victim 10s linear infinite}}
    @keyframes victim{{0%,34%{{fill:{g};opacity:1;transform:scale(1)}}38%,44%{{fill:{r};opacity:1;transform:scale(1.4)}}48%,96%{{fill:{r};opacity:0;transform:scale(0)}}100%{{fill:{g};opacity:1;transform:scale(1)}}}}
    .reborn{{fill:{a};opacity:0;animation:reborn 10s linear infinite}}
    @keyframes reborn{{0%,54%{{opacity:0;transform:scale(.3);fill:{a}}}58%,68%{{opacity:1;transform:scale(1);fill:{a}}}72%{{opacity:1;transform:scale(1);fill:{g}}}96%{{opacity:1;fill:{g}}}100%{{opacity:0;fill:{g}}}}}
    .ring{{fill:none;stroke:{r};stroke-width:2;opacity:0;animation:ring 10s ease-out infinite}}
    @keyframes ring{{0%,37%{{opacity:0;transform:scale(1)}}38%{{opacity:.9;transform:scale(1)}}50%{{opacity:0;transform:scale(3.4)}}100%{{opacity:0}}}}
    .s1,.s2,.s3,.s4{{opacity:0}}
    .s1{{animation:s1 10s linear infinite}} .s2{{animation:s2 10s linear infinite}} .s3{{animation:s3 10s linear infinite}} .s4{{animation:s4 10s linear infinite}}
    @keyframes s1{{0%,34%{{opacity:1}}36%,96%{{opacity:0}}98%,100%{{opacity:1}}}}
    @keyframes s2{{0%,35%{{opacity:0}}37%,49%{{opacity:1}}51%,100%{{opacity:0}}}}
    @keyframes s3{{0%,50%{{opacity:0}}52%,63%{{opacity:1}}65%,100%{{opacity:0}}}}
    @keyframes s4{{0%,64%{{opacity:0}}66%,94%{{opacity:1}}96%,100%{{opacity:0}}}}
    .cur{{animation:blink 1s steps(1) infinite}} @keyframes blink{{50%{{opacity:0}}}}
    {REDUCED_MOTION}
  </style>
</defs>
<rect width="1000" height="340" rx="16" fill="url(#bg)"/>
<rect width="1000" height="340" rx="16" fill="url(#grid)"/>
<ellipse cx="770" cy="170" rx="260" ry="170" fill="url(#glow)"/>
{stars}
<text x="48" y="62" font-family="{MONO}" font-size="13" fill="{P["amber_text"]}">// namaskaram  ·  <tspan font-family="{TELUGU}" font-size="15">నమస్కారం</tspan></text>
<g clip-path="url(#reveal)"><text x="48" y="122" font-family="{SANS}" font-weight="800" font-size="46" fill="url(#ttl)">Eknatha Reddy Puli</text></g>
<text x="48" y="162" font-family="{SANS}" font-weight="600" font-size="20" fill="{P["text"]}">Senior Staff Software Engineer</text>
<text x="48" y="190" font-family="{SANS}" font-size="15" fill="{P["muted"]}">Platform Engineering  ·  SRE  ·  Cloud Infrastructure</text>
<text x="48" y="234" font-family="{MONO}" font-size="15" fill="{P["pg"]}">&gt; 14+ years keeping production boring<tspan class="cur" fill="{P["pg"]}">█</tspan></text>
{pills}
<rect x="700" y="40" width="140" height="38" rx="9" fill="{P["cp"]}" stroke="{P["cp_stroke"]}" stroke-opacity=".6"/>
<text x="770" y="64" text-anchor="middle" font-family="{MONO}" font-size="12" fill="{P["cp_text"]}">control-plane</text>
{links}{node_svg}{pod_svg}{victim}{packets}
<g font-family="{MONO}" font-size="12" text-anchor="middle">
  <text class="s1" x="770" y="284" fill="{g}">● all pods Running</text>
  <text class="s2" x="770" y="284" fill="{r}">✕ chaos: pod killed</text>
  <text class="s3" x="770" y="284" fill="{P["amber_text"]}">↻ rescheduling…</text>
  <text class="s4" x="770" y="284" fill="{g}">✓ self-healed · SLO intact</text>
</g>
<text x="770" y="312" text-anchor="middle" font-family="{MONO}" font-size="10" fill="{P["faint"]}">break it on purpose, so it never breaks by surprise</text>
</svg>'''


# =========================================================== SVG: TERMINAL
def typed_clip(cid, x, y, h, cw, n, s, e, dur):
    kt = [0.0, s] + [s + (e - s) * i / n for i in range(1, n + 1)] + [0.96, 1.0]
    vals = [0, 0] + [round(cw * i, 1) for i in range(1, n + 1)] + [round(cw * n, 1), 0]
    return (f'<clipPath id="{cid}"><rect x="{x}" y="{y}" height="{h}" width="0"><animate attributeName="width" calcMode="discrete" '
            f'dur="{dur}s" repeatCount="indefinite" keyTimes="{";".join(f"{k:.4f}" for k in kt)}" values="{";".join(str(v) for v in vals)}"/></rect></clipPath>')


def vis(t_on, dur):
    return (f'<animate attributeName="opacity" dur="{dur}s" repeatCount="indefinite" values="0;0;1;1;0;0" '
            f'keyTimes="0;{t_on:.3f};{t_on+0.015:.3f};0.95;0.99;1"/>')


def terminal(P, tools, synced, live):
    DUR, CW, X = 18, 9.0, 226
    cmd1, cmd2 = "kubectl get pods -n eknathalabs", "cat /etc/eknathalabs/mission"
    y0, lh = 108, 28
    prompt = lambda y: (f'<text x="40" y="{y}" font-family="{MONO}" font-size="15" fill="{P["pg"]}">eknatha@platform'
                        f'<tspan fill="{P["muted"]}">:</tspan><tspan fill="{P["blue"]}">~</tspan><tspan fill="{P["muted"]}">$</tspan></text>')
    y2 = y0 + lh * (len(tools) + 2)
    clips = typed_clip("c1", X, y0 - 17, 22, CW, len(cmd1), 0.02, 0.12, DUR) + typed_clip("c2", X, y2 - 17, 22, CW, len(cmd2), 0.46, 0.55, DUR)
    cols = dict(name=40, ready=190, status=262, restarts=392, age=482, purpose=552)
    body = prompt(y0) + f'<g clip-path="url(#c1)"><text x="{X}" y="{y0}" font-family="{MONO}" font-size="15" fill="{P["text"]}">{cmd1}</text></g>'
    hy = y0 + lh
    body += (f'<g opacity="0">{vis(0.14, DUR)}<g font-family="{MONO}" font-size="14" fill="{P["dim"]}" font-weight="700">'
             + "".join(f'<text x="{cols[k]}" y="{hy}">{k.upper()}</text>' for k in cols) + '</g></g>')
    for i, t in enumerate(tools):
        y = hy + lh * (i + 1)
        ok = t["status"] == "Running"
        col = P["green"] if ok else (P["amber"] if t["status"] == "Pending" else P["red"])
        blink = '' if ok else '<animate attributeName="opacity" values="1;.35;1" dur="1.4s" repeatCount="indefinite"/>'
        body += (f'<g opacity="0">{vis(0.17 + 0.035 * i, DUR)}<g font-family="{MONO}" font-size="14">'
                 f'<text x="{cols["name"]}" y="{y}" fill="{P["text"]}">{t["name"]}</text>'
                 f'<text x="{cols["ready"]}" y="{y}" fill="{P["muted"]}">{"1/1" if ok else "0/1"}</text>'
                 f'<text x="{cols["status"]}" y="{y}" fill="{col}">● {t["status"]}{blink}</text>'
                 f'<text x="{cols["restarts"]}" y="{y}" fill="{P["muted"]}">{t["restarts"]}</text>'
                 f'<text x="{cols["age"]}" y="{y}" fill="{P["muted"]}">{t["age"]}</text>'
                 f'<text x="{cols["purpose"]}" y="{y}" fill="{P["dim"]}">{t["purpose"]}</text></g></g>')
    body += f'<g opacity="0">{vis(0.455, DUR)}{prompt(y2)}</g>'
    body += f'<g clip-path="url(#c2)"><text x="{X}" y="{y2}" font-family="{MONO}" font-size="15" fill="{P["text"]}">{cmd2}</text></g>'
    body += (f'<g opacity="0">{vis(0.57, DUR)}<text x="40" y="{y2+lh}" font-family="{MONO}" font-size="15" fill="{P["cyan"]}">'
             f'free  ·  offline-first  ·  no ads  ·  for everyone</text></g>')
    body += (f'<g opacity="0">{vis(0.63, DUR)}{prompt(y2+lh*2)}<rect x="{X}" y="{y2+lh*2-14}" width="9" height="18" fill="{P["text"]}">'
             f'<animate attributeName="opacity" values="1;0;1" keyTimes="0;.5;1" calcMode="discrete" dur="1s" repeatCount="indefinite"/></rect></g>')
    h = y2 + lh * 2 + 36
    tag = f"live · synced {synced}" if live else f"snapshot · {synced}"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {h}" width="1000" height="{h}" role="img" aria-labelledby="t">
<title id="t">kubectl get pods -n eknathalabs — EknathaLabs tools as pods with live status, age and 30-day commit activity</title>
<defs>{clips}<style>{REDUCED_MOTION}</style></defs>
<rect width="1000" height="{h}" rx="14" fill="{P["card"]}" stroke="{P["card_stroke"]}"/>
<path d="M0 14a14 14 0 0 1 14-14h972a14 14 0 0 1 14 14v26H0z" fill="{P["bar"]}"/>
<circle cx="26" cy="20" r="6" fill="#ff5f56"/><circle cx="48" cy="20" r="6" fill="#ffbd2e"/><circle cx="70" cy="20" r="6" fill="#27c93f"/>
<text x="500" y="25" text-anchor="middle" font-family="{MONO}" font-size="12" fill="{P["dim"]}">eknatha@platform: ~/eknathalabs</text>
<g>{body}</g>
<circle cx="40" cy="{h-18}" r="3.5" fill="{P["green"] if live else P["amber"]}"><animate attributeName="opacity" values="1;.3;1" dur="2s" repeatCount="indefinite"/></circle>
<text x="52" y="{h-14}" font-family="{MONO}" font-size="11" fill="{P["dim"]}">{tag}  ·  RESTARTS = commits in last 30 days</text>
</svg>'''


# ============================================================= SVG: PODS
def pods(P, days, synced, sample):
    weeks = weeks_from_days(days)
    W, L, T, PITCH = 1000, 56, 88, 17
    flat = [c for w in weeks for s in w if s for c in [s[1]]]
    mx = max(flat) if flat and max(flat) > 0 else 1
    level = lambda c: 0 if c <= 0 else min(4, max(1, math.ceil(4 * c / mx)))
    last_active = None
    for wi, w in enumerate(weeks):
        for di, s in enumerate(w):
            if s and s[1] > 0:
                last_active = (wi, di)
    cols = ""
    months, last_lbl = "", -9
    for wi, w in enumerate(weeks):
        first = next((s for s in w if s), None)
        if first and wi - last_lbl >= 3 and (dt.date.fromisoformat(first[0]).day <= 7 or wi == 0):
            if not (wi == 0 and dt.date.fromisoformat(first[0]).day > 7 and len(weeks) > 1 and False):
                months += f'<text x="{L+wi*PITCH}" y="{T-12}" font-family="{MONO}" font-size="10" fill="{P["dim"]}">{dt.date.fromisoformat(first[0]).strftime("%b")}</text>'
                last_lbl = wi
        cs = ""
        for di, s in enumerate(w):
            if not s:
                continue
            cx, cy = L + wi * PITCH + 7, T + di * PITCH + 7
            lv = level(s[1])
            if lv == 0:
                cs += f'<circle cx="{cx}" cy="{cy}" r="3.6" fill="{P["empty"]}"/>'
            else:
                cs += f'<circle cx="{cx}" cy="{cy}" r="6.2" fill="{P["ramp"][lv-1]}"/>'
        cols += f'<g class="col" style="animation-delay:{wi*0.09:.2f}s">{cs}</g>'
    ring = ""
    if last_active:
        wi, di = last_active
        ring = f'<circle cx="{L+wi*PITCH+7}" cy="{T+di*PITCH+7}" r="6.2" class="latest"/>'
    labels = "".join(f'<text x="{L-10}" y="{T+i*PITCH+11}" text-anchor="end" font-family="{MONO}" font-size="10" fill="{P["dim"]}">{n}</text>'
                     for i, n in [(1, "Mon"), (3, "Wed"), (5, "Fri")])
    flat_days = [(s[0], s[1]) for w in weeks for s in w if s]
    total = sum(c for _d, c in flat_days)
    cur, longest = streaks(flat_days)
    if sample:
        stats = "awaiting first sync · sample pattern"
    else:
        stats = f"{total:,} contributions in the last year  ·  current streak {cur}d  ·  longest {longest}d"
    ly = T + 7 * PITCH + 26
    legend = f'<text x="{W-196}" y="{ly}" font-family="{MONO}" font-size="10" fill="{P["dim"]}">fewer</text>'
    for i, c in enumerate([P["empty"]] + P["ramp"]):
        legend += f'<circle cx="{W-150+i*18}" cy="{ly-4}" r="{3.6 if i==0 else 6}" fill="{c}"/>'
    legend += f'<text x="{W-52}" y="{ly}" font-family="{MONO}" font-size="10" fill="{P["dim"]}">more</text>'
    H = ly + 38
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t">
<title id="t">Contribution calendar drawn as a field of pods: one pod per day, greener means more commits shipped. {stats}</title>
<defs><style>
  .col{{animation:scan 8s linear infinite}}
  @keyframes scan{{0%,16%,100%{{opacity:1}}6%{{opacity:.28}}}}
  .latest{{fill:none;stroke:{P["cyan"]};stroke-width:2;transform-box:fill-box;transform-origin:center;animation:lp 2s ease-out infinite}}
  @keyframes lp{{0%{{opacity:.95;transform:scale(1)}}100%{{opacity:0;transform:scale(2.6)}}}}
  .cur{{animation:blink 1s steps(1) infinite}} @keyframes blink{{50%{{opacity:0}}}}
  {REDUCED_MOTION}
</style></defs>
<rect width="{W}" height="{H}" rx="14" fill="{P["card"]}" stroke="{P["card_stroke"]}"/>
<text x="32" y="38" font-family="{MONO}" font-size="14" fill="{P["pg"]}">$ <tspan fill="{P["text"]}">kubectl get pods -A --watch</tspan><tspan class="cur" fill="{P["text"]}"> █</tspan><tspan fill="{P["dim"]}">   # 1 pod = 1 day</tspan></text>
{months}{labels}{cols}{ring}
<text x="{L}" y="{ly}" font-family="{MONO}" font-size="12" fill="{P["muted"]}">{stats}</text>
{legend}
</svg>'''


# ========================================================= SVG: ROLLOUT
def rollout(P):
    DUR = 12
    revs = [("2010", "RCS · Genisys", "trainee"), ("2011–17", "Ericsson", ""), ("2017–19", "Subex", ""),
            ("2019–22", "AT&amp;T · Xandr", "→ Microsoft"), ("2022–23", "Sterlite", ""), ("2023 → now", "IBM", "current · Running")]
    xs = [90 + i * 164 for i in range(6)]
    t0, t1 = 0.05, 0.60
    g = ""
    for i, (yr, org, sub) in enumerate(revs):
        t, last = t0 + (t1 - t0) * i / 5, i == 5
        col = P["green"] if last else P["cyan"]
        g += (f'<g opacity=".3"><animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" values=".3;.3;1;1;.3;.3" keyTimes="0;{t:.3f};{t+0.03:.3f};0.95;0.99;1"/>'
              f'<text x="{xs[i]}" y="86" text-anchor="middle" font-family="{MONO}" font-size="11" fill="{P["dim"]}">rev {i+1}</text>'
              f'<text x="{xs[i]}" y="108" text-anchor="middle" font-family="{MONO}" font-size="13" fill="{P["muted"]}">{yr}</text>'
              f'<text x="{xs[i]}" y="166" text-anchor="middle" font-family="{SANS}" font-weight="700" font-size="15" fill="{P["text"]}">{org}</text>'
              f'<text x="{xs[i]}" y="186" text-anchor="middle" font-family="{SANS}" font-size="12" fill="{P["green"] if last else P["dim"]}">{sub}</text></g>'
              f'<circle cx="{xs[i]}" cy="132" r="9" fill="{P["dot_bg"]}" stroke="{P["dot_stroke"]}" stroke-width="2"/>'
              f'<circle cx="{xs[i]}" cy="132" r="9" fill="{col}" opacity="0"><animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" values="0;0;1;1;0;0" keyTimes="0;{t:.3f};{t+0.03:.3f};0.95;0.99;1"/></circle>')
    g += f'<circle cx="910" cy="132" r="9" fill="none" stroke="{P["green"]}" class="pulse"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 230" width="1000" height="230" role="img" aria-labelledby="t">
<title id="t">Career as a rollout history: RCS and Genisys, Ericsson, Subex, AT&amp;T/Xandr/Microsoft, Sterlite, IBM</title>
<defs><style>.pulse{{transform-box:fill-box;transform-origin:center;animation:p 2s ease-out infinite}}@keyframes p{{0%{{opacity:.9;transform:scale(1)}}100%{{opacity:0;transform:scale(2.6)}}}}{REDUCED_MOTION}</style></defs>
<rect width="1000" height="230" rx="14" fill="{P["card"]}" stroke="{P["card_stroke"]}"/>
<text x="32" y="38" font-family="{MONO}" font-size="14" fill="{P["pg"]}">$ <tspan fill="{P["text"]}">kubectl rollout history deployment/eknatha</tspan></text>
<line x1="90" y1="132" x2="910" y2="132" stroke="{P["track"]}" stroke-width="3" stroke-linecap="round"/>
<line x1="90" y1="132" x2="910" y2="132" stroke="{P["cyan"]}" stroke-width="3" stroke-linecap="round" stroke-dasharray="820" stroke-dashoffset="820" opacity="0">
  <animate attributeName="stroke-dashoffset" dur="{DUR}s" repeatCount="indefinite" values="820;820;0;0;0" keyTimes="0;{t0};{t1};0.95;1"/>
  <animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" values="0;1;1;0;0" keyTimes="0;0.03;0.95;0.99;1"/>
</line>
{g}
<circle cy="132" r="5" fill="{P["text"]}" opacity="0"><animate attributeName="cx" dur="{DUR}s" repeatCount="indefinite" values="90;90;910;910" keyTimes="0;{t0};{t1};1"/>
  <animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" values="0;1;1;0;0" keyTimes="0;{t0};{t1};{t1+0.04};1"/></circle>
</svg>'''


# ========================================================== SVG: FOOTER
def footer(P):
    d = "M0,70 "
    for k in range(4):
        x0 = k * 250
        d += f"H{x0+90} l10,-10 l10,10 H{x0+130} l6,8 l10,-58 l10,70 l8,-20 H{x0+190} l12,-14 l12,14 H{x0+250} "
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 190" width="1000" height="190" role="img" aria-labelledby="t">
<title id="t">Heartbeat line — reliability is a feature</title>
<defs><linearGradient id="ecg" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{P["cyan"]}"/><stop offset="1" stop-color="{P["green"]}"/></linearGradient></defs>
<rect width="1000" height="190" rx="14" fill="{P["card"]}" stroke="{P["card_stroke"]}"/>
<path d="{d}" pathLength="1000" fill="none" stroke="{P["track"]}" stroke-width="2" stroke-linejoin="round"/>
<path d="{d}" pathLength="1000" fill="none" stroke="url(#ecg)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="220 2000" stroke-dashoffset="220">
  <animate attributeName="stroke-dashoffset" from="220" to="-1000" dur="3.2s" repeatCount="indefinite"/></path>
<text x="500" y="140" text-anchor="middle" font-family="{SANS}" font-weight="700" font-size="22" fill="{P["text"]}">Reliability is a feature. Ship it boring.</text>
<text x="500" y="166" text-anchor="middle" font-family="{MONO}" font-size="12" fill="{P["dim"]}">built in Bengaluru  ·  free tools at eknathalabs.com</text>
</svg>'''


# ============================================================== MAIN
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="assets")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--state", default="data/state.json")
    ap.add_argument("--offline", action="store_true", help="skip all network calls, reuse last state")
    a = ap.parse_args()

    cfg = load_json(a.config, None)
    if not cfg:
        sys.exit(f"cannot read {a.config}")
    state = load_json(a.state, {})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    user = os.environ.get("GH_USER") or cfg["user"]

    online = (not a.offline) and http("https://api.github.com/rate_limit", gh_headers(token), timeout=8)[0] == 200
    print("network:", "online" if online else "offline (reusing last state)")

    tools = collect_tools(cfg, state, token, online)
    live = online or state.get("live", False)

    days = fetch_contribs(user, token) if online else None
    sample = False
    if days:
        state["contribs"] = days
    else:
        days = state.get("contribs")
    if not days:
        days, sample = sample_days(), True

    synced = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d") if online else state.get("synced", "never")
    state.update(tools={t["name"]: t for t in tools}, synced=synced, live=bool(live))

    os.makedirs(a.out, exist_ok=True)
    for P in (DARK, LIGHT):
        files = {"hero": hero(P), "terminal": terminal(P, tools, synced, live),
                 "pods": pods(P, days, synced, sample), "rollout": rollout(P), "footer": footer(P)}
        for name, svg in files.items():
            with open(os.path.join(a.out, f"{name}-{P['name']}.svg"), "w", encoding="utf-8") as f:
                f.write(svg)
    os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
    with open(a.state, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    print("tools:", ", ".join(f'{t["name"]}={t["status"]}' for t in tools), "| contribs:", "sample" if sample else len(days))


if __name__ == "__main__":
    main()
