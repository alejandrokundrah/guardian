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
# Guardian DNS Installer  —  {s['name']}
# Run as Administrator in PowerShell
# ================================================================

$ErrorActionPreference = "Stop"
$name   = "{s['name']}"
$dohUrl = "{doh_url}"

Write-Host ""
Write-Host "  Guardian DNS Installer" -ForegroundColor Cyan
Write-Host "  Student : $name"        -ForegroundColor White
Write-Host "  Server  : $dohUrl"      -ForegroundColor DarkGray
Write-Host ""

# Verify admin rights
$admin = ([Security.Principal.WindowsPrincipal]
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {{
    Write-Host "  ERROR: Run this script as Administrator" -ForegroundColor Red
    exit 1
}}

# Step 1 — Register DoH template
Write-Host "  [1/4] Registering DNS-over-HTTPS template..." -ForegroundColor Yellow
foreach ($ip in @("8.8.8.8", "8.8.4.4")) {{
    try {{
        Add-DnsClientDohServerAddress `
            -ServerAddress $ip `
            -DohTemplate   $dohUrl `
            -AllowFallbackToUdp $False `
            -AutoUpgrade        $True `
            -ErrorAction SilentlyContinue
    }} catch {{
        Write-Host "  (template may already exist, continuing)" -ForegroundColor DarkGray
    }}
}}

# Step 2 — Apply to all active adapters
Write-Host "  [2/4] Applying to network adapters..." -ForegroundColor Yellow
Get-NetAdapter | Where-Object {{ $_.Status -eq "Up" }} | ForEach-Object {{
    Set-DnsClientServerAddress -InterfaceIndex $_.InterfaceIndex `
        -ServerAddresses @("8.8.8.8", "8.8.4.4")
}}

# Step 3 — Enforce DoH via Group Policy registry key
Write-Host "  [3/4] Locking DNS settings..." -ForegroundColor Yellow
$reg = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient"
if (-not (Test-Path $reg)) {{ New-Item -Path $reg -Force | Out-Null }}
Set-ItemProperty -Path $reg -Name "DoHPolicy"           -Value 2 -Type DWord
Set-ItemProperty -Path $reg -Name "EnableAutoDoh"       -Value 2 -Type DWord

# Step 4 — Flush cache
Write-Host "  [4/4] Flushing DNS cache..." -ForegroundColor Yellow
Clear-DnsClientCache
ipconfig /flushdns | Out-Null

Write-Host ""
Write-Host "  SUCCESS — Guardian DNS installed for $name" -ForegroundColor Green
Write-Host "  This device is now protected."              -ForegroundColor Green
Write-Host ""
'''

    return Response(
        script,
        content_type='text/plain',
        headers={
            'Content-Disposition':
                f'attachment; filename="Install_Guardian_{s["name"]}.ps1"'
        },
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


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
