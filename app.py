import base64
import os
import uuid

from flask import (Flask, Response, abort, jsonify, redirect,
                   render_template, request, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from database import (add_student, block_all_students, delete_student,
                      get_all_students, get_logs, get_student_by_id,
                      get_watch_minutes_today, init_db, toggle_emergency_block,
                      update_student)
from dns_resolver import handle_query

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# Trust reverse-proxy headers (required for correct HTTPS URL on Railway/Render)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


# ─────────────────────────────────────────────────────────────────────────────
# DNS-over-HTTPS endpoint
# Devices point their DoH setting to:  https://your-domain.com/dns/<TOKEN>
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/dns/<token>', methods=['GET', 'POST'])
def doh(token):
    if request.method == 'GET':
        # RFC 8484 §4.1 — base64url-encoded wire format in ?dns=
        b64 = request.args.get('dns', '')
        b64 += '=' * (-len(b64) % 4)          # restore padding
        try:
            data = base64.urlsafe_b64decode(b64)
        except Exception:
            abort(400)
    else:
        # RFC 8484 §4.1 — binary DNS message in POST body
        ct = request.content_type or ''
        if 'dns-message' not in ct:
            abort(415)
        data = request.get_data()

    result = handle_query(data, token)
    if result:
        return Response(result, content_type='application/dns-message')
    abort(500)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard pages
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', students=get_all_students())


@app.route('/student/<int:sid>')
def student_detail(sid):
    student = get_student_by_id(sid)
    if not student:
        return redirect(url_for('index'))
    logs = get_logs(sid, limit=60)
    return render_template('student.html', student=student, logs=logs)


@app.route('/logs')
def logs_page():
    return render_template('logs.html',
                           logs=get_logs(limit=200),
                           students=get_all_students())


# ─────────────────────────────────────────────────────────────────────────────
# REST API
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/students', methods=['GET'])
def api_list():
    return jsonify(get_all_students())


@app.route('/api/students', methods=['POST'])
def api_add():
    d = request.json or {}
    if not d.get('name'):
        return jsonify({'error': 'name required'}), 400
    sid, token = add_student(
        name=d['name'],
        allowed_days=d.get('allowed_days', list(range(7))),
        start_hour=int(d.get('start_hour', 19)),
        end_hour=int(d.get('end_hour', 22)),
        daily_limit=int(d.get('daily_limit', 120)),
    )
    return jsonify({'id': sid, 'token': token}), 201


@app.route('/api/students/<int:sid>', methods=['PUT'])
def api_update(sid):
    d = request.json or {}
    update_student(
        sid,
        name=d.get('name'),
        allowed_days=d.get('allowed_days'),
        start_hour=d.get('start_hour'),
        end_hour=d.get('end_hour'),
        daily_limit_minutes=d.get('daily_limit'),
    )
    return jsonify({'ok': True})


@app.route('/api/students/<int:sid>', methods=['DELETE'])
def api_delete(sid):
    delete_student(sid)
    return jsonify({'ok': True})


@app.route('/api/students/<int:sid>/emergency', methods=['POST'])
def api_emergency(sid):
    blocked = request.json.get('blocked', True)
    toggle_emergency_block(sid, blocked)
    return jsonify({'ok': True})


@app.route('/api/emergency-all', methods=['POST'])
def api_emergency_all():
    blocked = request.json.get('blocked', True)
    block_all_students(blocked)
    return jsonify({'ok': True})


@app.route('/api/students/<int:sid>/status')
def api_status(sid):
    s = get_student_by_id(sid)
    if not s:
        abort(404)
    return jsonify({
        'id':               s['id'],
        'name':             s['name'],
        'minutes_today':    s['minutes_today'],
        'daily_limit':      s['daily_limit_minutes'],
        'emergency_blocked': s['emergency_blocked'],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Device profile generators
# ─────────────────────────────────────────────────────────────────────────────

def _doh_url(sid):
    s = get_student_by_id(sid)
    base = request.host_url.rstrip('/')
    return s, f"{base}/dns/{s['token']}"


@app.route('/api/students/<int:sid>/profile/ios')
def ios_profile(sid):
    s, doh_url = _doh_url(sid)
    if not s:
        abort(404)

    payload_uuid = str(uuid.uuid4())
    profile_uuid = str(uuid.uuid4())

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>DNSSettings</key>
            <dict>
                <key>DNSProtocol</key>
                <string>HTTPS</string>
                <key>ServerURL</key>
                <string>{doh_url}</string>
                <key>ServerAddresses</key>
                <array>
                    <string>8.8.8.8</string>
                    <string>8.8.4.4</string>
                </array>
            </dict>
            <key>PayloadDescription</key>
            <string>Guardian DNS for {s['name']}</string>
            <key>PayloadDisplayName</key>
            <string>Guardian DNS</string>
            <key>PayloadIdentifier</key>
            <string>com.guardian.dns.{s['token'][:10]}</string>
            <key>PayloadType</key>
            <string>com.apple.dnsSettings.managed</string>
            <key>PayloadUUID</key>
            <string>{payload_uuid}</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
        </dict>
    </array>
    <key>PayloadDescription</key>
    <string>Guardian Parental Control DNS for {s['name']}</string>
    <key>PayloadDisplayName</key>
    <string>Guardian — {s['name']}</string>
    <key>PayloadIdentifier</key>
    <string>com.guardian.profile.{s['token'][:10]}</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>'''

    return Response(
        xml,
        content_type='application/x-apple-aspen-config',
        headers={
            'Content-Disposition':
                f'attachment; filename="Guardian_{s["name"]}.mobileconfig"'
        },
    )


@app.route('/api/students/<int:sid>/profile/windows')
def windows_profile(sid):
    s, doh_url = _doh_url(sid)
    if not s:
        abort(404)

    script = f'''# ================================================================
# Guardian Installer — {s['name']}
# Right-click this file and select "Run with PowerShell"
# ================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  Guardian Parental Control" -ForegroundColor Cyan
Write-Host "  Installing for: {s['name']}" -ForegroundColor White
Write-Host ""

# Check admin
$admin = ([Security.Principal.WindowsPrincipal]
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {{
    Write-Host "  ERROR: Please right-click and Run as Administrator" -ForegroundColor Red
    pause
    exit 1
}}

# ── Step 1 — Create agent file ────────────────────────────────
Write-Host "  [1/6] Creating Guardian agent..." -ForegroundColor Yellow
New-Item -Path "C:\\Guardian" -ItemType Directory -Force | Out-Null

$agentCode = @'
import requests, time

SERVER = "https://guardian-1hfv.onrender.com"
TOKEN  = "{s['token']}"
HOSTS  = r"C:\\Windows\\System32\\drivers\\etc\\hosts"

NETFLIX_DOMAINS = [
    "netflix.com", "www.netflix.com", "api.netflix.com",
    "nflxvideo.net", "nflximg.net", "nflxext.com",
    "nflxso.net", "fast.com", "netflix.net",
    "nflxso.com", "nflximg.com"
]

MARKER_START = "# --- GUARDIAN START ---"
MARKER_END   = "# --- GUARDIAN END ---"

def clean_hosts():
    with open(HOSTS, "r") as f:
        lines = f.readlines()
    out, inside = [], False
    for line in lines:
        if MARKER_START in line:  inside = True
        elif MARKER_END in line:  inside = False
        elif not inside:          out.append(line)
    with open(HOSTS, "w") as f:
        f.writelines(out)

def block_netflix():
    clean_hosts()
    with open(HOSTS, "a") as f:
        f.write(f"\\n{{MARKER_START}}\\n")
        for d in NETFLIX_DOMAINS:
            f.write(f"0.0.0.0 {{d}}\\n")
        f.write(f"{{MARKER_END}}\\n")
    print("[GUARDIAN] BLOCKED")

def allow_netflix():
    clean_hosts()
    print("[GUARDIAN] ALLOWED")

def check():
    try:
        r = requests.get(
            f"{{SERVER}}/api/students/status-by-token/{{TOKEN}}",
            timeout=5
        )
        return r.json().get("allowed", False)
    except:
        return False

print("[GUARDIAN] Agent running...")
while True:
    try:
        if check(): allow_netflix()
        else:        block_netflix()
    except Exception as e:
        print(f"[GUARDIAN] Error: {{e}}")
        block_netflix()
    time.sleep(60)
'@

$agentCode | Out-File -FilePath "C:\\Guardian\\agent.py" -Encoding UTF8 -Force
Write-Host "  [1/6] Agent created" -ForegroundColor Green

# ── Step 2 — Install Python requests ─────────────────────────
Write-Host "  [2/6] Installing dependencies..." -ForegroundColor Yellow
pip install requests --quiet
Write-Host "  [2/6] Dependencies installed" -ForegroundColor Green

# ── Step 3 — Disable browser DoH so hosts file works ─────────
Write-Host "  [3/6] Disabling browser built-in DNS..." -ForegroundColor Yellow

# Chrome
$chromePol = "HKLM:\\SOFTWARE\\Policies\\Google\\Chrome"
if (-not (Test-Path $chromePol)) {{ New-Item -Path $chromePol -Force | Out-Null }}
Set-ItemProperty -Path $chromePol -Name "DnsOverHttpsMode" -Value "off" -Type String

# Edge
$edgePol = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Edge"
if (-not (Test-Path $edgePol)) {{ New-Item -Path $edgePol -Force | Out-Null }}
Set-ItemProperty -Path $edgePol -Name "DnsOverHttpsMode" -Value "off" -Type String

# Firefox
$ffPol = "HKLM:\\SOFTWARE\\Policies\\Mozilla\\Firefox"
if (-not (Test-Path $ffPol)) {{ New-Item -Path $ffPol -Force | Out-Null }}
Set-ItemProperty -Path $ffPol -Name "DNSOverHTTPS" -Value 0 -Type DWord

Write-Host "  [3/6] Browser DNS locked" -ForegroundColor Green

# ── Step 4 — Register as Windows startup task ─────────────────
Write-Host "  [4/6] Registering startup task..." -ForegroundColor Yellow

$action   = New-ScheduledTaskAction -Execute "python" -Argument "C:\\Guardian\\agent.py"
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
              -ExecutionTimeLimit 0 `
              -RestartCount 99 `
              -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName "GuardianAgent" `
  -Action   $action `
  -Trigger  $trigger `
  -Settings $settings `
  -RunLevel Highest `
  -Force | Out-Null

Write-Host "  [4/6] Startup task registered" -ForegroundColor Green

# ── Step 5 — Lock DNS settings ────────────────────────────────
Write-Host "  [5/6] Applying DNS settings..." -ForegroundColor Yellow
Set-DnsClientServerAddress -InterfaceAlias "Wi-Fi" `
  -ServerAddresses @("8.8.8.8","8.8.4.4") -ErrorAction SilentlyContinue
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" `
  -ServerAddresses @("8.8.8.8","8.8.4.4") -ErrorAction SilentlyContinue
Clear-DnsClientCache
Write-Host "  [5/6] DNS configured" -ForegroundColor Green

# ── Step 6 — Start the agent now ─────────────────────────────
Write-Host "  [6/6] Starting Guardian agent..." -ForegroundColor Yellow
Start-ScheduledTask -TaskName "GuardianAgent"
Write-Host "  [6/6] Agent running in background" -ForegroundColor Green

Write-Host ""
Write-Host "  Guardian installed successfully for {s['name']}!" -ForegroundColor Green
Write-Host "  Netflix will be controlled automatically from now on." -ForegroundColor Green
Write-Host "  You can close this window." -ForegroundColor DarkGray
Write-Host ""
pause
'''

    return Response(
        script,
        content_type='text/plain',
        headers={{
            'Content-Disposition':
                f'attachment; filename="Install_Guardian_{s["name"]}.ps1"'
        }},
    )

@app.route('/api/students/<int:sid>/profile/android')
def android_config(sid):
    s, doh_url = _doh_url(sid)
    if not s:
        abort(404)
    host = request.host
    return jsonify({
        'student': s['name'],
        'doh_url': doh_url,
        'setup': {
            'Chrome (easiest)': (
                'Chrome → ⋮ Settings → Privacy and security → Security → '
                'Use secure DNS → With (Custom) → paste the URL above'
            ),
            'Firefox': (
                'Firefox → ☰ Settings → General → Network Settings → '
                'Enable DNS over HTTPS → Custom → paste the URL above'
            ),
            'Samsung Internet': (
                'Settings → Privacy → Custom DNS → paste the URL above'
            ),
            'System-wide (Android 9+)': (
                f'Settings → Network → Private DNS → '
                f'Private DNS provider hostname → enter: {host}  '
                f'(Note: system-wide DoT only uses the hostname; '
                f'for full per-student rules use the browser method above)'
            ),
        },
    })
@app.route('/api/students/status-by-token/<token>')
def api_status_by_token(token):
    from rule_engine import check_access
    allowed, reason = check_access('netflix.com', token)
    return jsonify({'allowed': allowed, 'reason': reason})

# ─────────────────────────────────────────────────────────────────────────────

# ✅ Fixed — runs on every startup including Gunicorn
init_db()  # ← move this line UP, outside the if block

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)