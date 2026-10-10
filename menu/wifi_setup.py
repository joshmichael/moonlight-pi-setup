#!/usr/bin/env python3
"""Phone Wi-Fi setup: a temporary hotspot and a setup page.

The Pi starts its own Wi-Fi network. A phone joins it (the TV shows a QR code),
the phone opens a setup page (phones do this by themselves on networks like
this), and the user picks their Wi-Fi and types the password. The Pi then
joins that network and this service exits.

Runs as root, started from the Moonlight menu as moonlight-wifi-setup.service.
Progress is written to /run/moonlight-wifi-setup/status.json for the menu.

Installed by moonlight-pi-setup.
"""
import html
import json
import logging
import os
import queue
import secrets
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import network  # noqa: E402
from network import HOTSPOT_NAME, NetworkError, run  # noqa: E402

log = logging.getLogger('moonlight-wifi-setup')

STATUS_DIR = '/run/moonlight-wifi-setup'
STATUS_FILE = os.path.join(STATUS_DIR, 'status.json')
DNSMASQ_CONF = '/etc/NetworkManager/dnsmasq-shared.d/moonlight-wifi-setup.conf'
HOTSPOT_IP = '10.42.0.1'
URL = 'http://%s' % HOTSPOT_IP
IDLE_TIMEOUT = 15 * 60
PASSWORD_CHARS = 'abcdefghjkmnpqrstuvwxyz23456789'


class Status:
    def __init__(self):
        os.makedirs(STATUS_DIR, exist_ok=True)
        self.data = {}

    def set(self, state, **kw):
        self.data.update(kw)
        self.data['state'] = state
        self.data['updated'] = time.time()
        tmp = STATUS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATUS_FILE)
        log.info('status: %s %s', state, kw.get('message', ''))


def hotspot_ssid():
    suffix = ''
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('Serial'):
                    suffix = line.split(':')[1].strip()[-4:].upper()
    except OSError:
        pass
    return 'Moonlight-Setup-' + (suffix or secrets.token_hex(2).upper())


class Hotspot:
    def __init__(self, dev, ssid, password):
        self.dev, self.ssid, self.password = dev, ssid, password

    def start(self):
        run(['nmcli', 'connection', 'delete', 'id', HOTSPOT_NAME])
        rc, _, err = run(['nmcli', 'connection', 'add', 'type', 'wifi', 'ifname', self.dev,
                          'con-name', HOTSPOT_NAME, 'autoconnect', 'no', 'ssid', self.ssid,
                          '802-11-wireless.mode', 'ap', '802-11-wireless.band', 'bg',
                          'ipv4.method', 'shared', 'ipv4.addresses', HOTSPOT_IP + '/24',
                          'ipv6.method', 'disabled',
                          'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', self.password,
                          'wifi-sec.proto', 'rsn', 'wifi-sec.pairwise', 'ccmp', 'wifi-sec.group', 'ccmp',
                          'wifi-sec.pmf', 'disable'])
        if rc != 0:
            raise NetworkError('Could not create the setup network: ' + err)
        rc, _, err = run(['nmcli', '--wait', '30', 'connection', 'up', 'id', HOTSPOT_NAME], 45)
        if rc != 0:
            raise NetworkError('Could not start the setup network: ' + err)

    def stop(self):
        run(['nmcli', 'connection', 'down', 'id', HOTSPOT_NAME], 30)

    def remove(self):
        self.stop()
        run(['nmcli', 'connection', 'delete', 'id', HOTSPOT_NAME])


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonlight Pi Wi-Fi setup</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0d0f15;color:#eceff5;
margin:0;padding:24px;line-height:1.45}
main{max-width:480px;margin:0 auto}
h1{font-size:1.5rem;margin:.2em 0 .6em}
p{color:#98a0b1}
label{display:block;font-weight:600;margin:1.2em 0 .4em}
select,input[type=text],input[type=password]{width:100%;box-sizing:border-box;font-size:1.1rem;padding:.75em;
border-radius:12px;border:1px solid #39404f;background:#1a1e28;color:#eceff5}
.row{display:flex;align-items:center;gap:.5em;margin-top:.6em;color:#98a0b1}
button{width:100%;margin-top:1.6em;font-size:1.15rem;font-weight:700;padding:.9em;border:0;border-radius:12px;
background:#6284ff;color:#fff}
.err{background:#5a2424;border-radius:12px;padding:.8em 1em;color:#fff}
.ok{background:#1f4a33;border-radius:12px;padding:.8em 1em;color:#fff}
#otherbox{display:none}
.nets{display:flex;flex-direction:column;gap:8px}
.net{display:flex;align-items:center;gap:12px;margin:0;font-weight:400;padding:.85em 1em;border-radius:12px;
background:#1a1e28;border:2px solid #1a1e28;cursor:pointer}
.net input{accent-color:#6284ff;width:20px;height:20px;margin:0;flex:none}
.net:has(input:checked){border-color:#6284ff;background:#20263a}
.name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lock{width:14px;height:18px;fill:#98a0b1;flex:none}
.bars{display:flex;align-items:flex-end;gap:3px;height:18px;flex:none}
.bars i{display:block;width:5px;border-radius:1px;background:#3a4152}
.bars i:nth-child(1){height:6px}.bars i:nth-child(2){height:10px}
.bars i:nth-child(3){height:14px}.bars i:nth-child(4){height:18px}
.b1 i:nth-child(-n+1),.b2 i:nth-child(-n+2),.b3 i:nth-child(-n+3),.b4 i:nth-child(-n+4){background:#eceff5}
</style></head><body><main>
%(body)s
</main>
<script>
var o=document.getElementById('otherbox');
function upd(){var c=document.querySelector('input[name=ssid]:checked');
if(o){o.style.display=(c&&c.value==='__other__')?'block':'none';}}
document.querySelectorAll('input[name=ssid]').forEach(function(r){r.addEventListener('change',upd);});upd();
var sh=document.getElementById('show'),pw=document.getElementById('password');
if(sh&&pw){sh.addEventListener('change',function(){pw.type=sh.checked?'text':'password';});}
</script></body></html>"""

LOCK_SVG = ('<svg class="lock" viewBox="0 0 14 18" aria-label="Password protected"><path d="M3 8V5.5a4 4 0 0 1 '
            '8 0V8h-2V5.5a2 2 0 0 0-4 0V8z"/><rect x="0" y="7.5" width="14" height="10.5" rx="2.5"/></svg>')


def signal_level(strength):
    """0-4 bars, the same as the TV shows."""
    return 0 if strength <= 0 else 1 + min(3, strength // 26)


def form_body(networks, error):
    rows = []
    for i, n in enumerate(networks):
        rows.append('<label class="net"><input type="radio" name="ssid" value="%s"%s>'
                    '<span class="name">%s</span>%s<span class="bars b%d" aria-label="Signal %d of 4">'
                    '<i></i><i></i><i></i><i></i></span></label>' % (
                        html.escape(n['ssid'], quote=True), ' checked' if i == 0 else '',
                        html.escape(n['ssid']), LOCK_SVG if n['security'] else '',
                        signal_level(n['signal']), signal_level(n['signal'])))
    rows.append('<label class="net"><input type="radio" name="ssid" value="__other__"%s>'
                '<span class="name">Other (hidden) network…</span></label>' % ('' if networks else ' checked'))
    err = '<p class="err">Couldn\'t connect: %s</p>' % html.escape(error) if error else ''
    return """<h1>Connect your Moonlight Pi to Wi-Fi</h1>
%s<p>Choose the network the Pi should join, and type its password.</p>
<form method="post" action="/connect">
<label>Network</label>
<div class="nets">%s</div>
<div id="otherbox"><label for="other">Network name</label>
<input type="text" id="other" name="other" autocapitalize="off" autocorrect="off" maxlength="32"></div>
<label for="password">Password</label>
<input type="password" id="password" name="password" autocapitalize="off" autocorrect="off" maxlength="63"
 placeholder="Leave empty if there is none">
<div class="row"><input type="checkbox" id="show"><label for="show" style="margin:0;font-weight:400">Show
password</label></div>
<button type="submit">Connect</button>
</form>""" % (err, ''.join(rows))


def connecting_body(ssid):
    return """<h1>Connecting to %s…</h1>
<p class="ok">The Pi is joining your Wi-Fi now. Your phone will leave this setup network in a moment, which is
normal. Check the TV to see when it's connected.</p>
<p>If the password was wrong, the setup network comes back: join it again to retry.</p>""" % html.escape(ssid)


class Portal:
    def __init__(self, networks, requests):
        self.networks = networks
        self.requests = requests
        self.last_error = ''
        portal = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'MoonlightPi'

            def log_message(self, fmt, *args):
                log.debug('http: ' + fmt, *args)

            def _allowed(self):
                return self.client_address[0].startswith('10.42.0.')

            def _send(self, body, code=200):
                data = PAGE.replace('%(body)s', body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not self._allowed():
                    self.send_error(403)
                    return
                # Answering every address with the setup page makes phones show it by themselves.
                self._send(form_body(portal.networks, portal.last_error))

            def do_POST(self):
                if not self._allowed():
                    self.send_error(403)
                    return
                length = int(self.headers.get('Content-Length') or 0)
                if length > 4096:
                    self.send_error(413)
                    return
                fields = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))
                ssid = (fields.get('ssid') or [''])[0]
                password = (fields.get('password') or [''])[0]
                hidden = ssid == '__other__'
                if hidden:
                    ssid = (fields.get('other') or [''])[0].strip()
                known = next((n for n in portal.networks if n['ssid'] == ssid), None)
                security = known['security'] if known else ('WPA2' if password else '')
                error = ''
                if not ssid:
                    error = 'Choose a network first.'
                else:
                    try:
                        error = network.validate_password(security, password)
                    except NetworkError as e:
                        error = str(e)
                if error:
                    self._send(form_body(portal.networks, error))
                    return
                self._send(connecting_body(ssid))
                portal.requests.put({'ssid': ssid, 'password': password, 'security': security,
                                     'hidden': hidden or known is None})

        self.server = ThreadingHTTPServer(('0.0.0.0', 80), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def wait_for_ssid(net, ssid, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if any(n['ssid'] == ssid for n in net.scan(rescan=True)):
                return True
        except NetworkError:
            pass
        time.sleep(1)
    return False


def main():
    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
    status = Status()
    status.set('starting')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())

    net = network.Network()
    if not net.wifi_dev:
        status.set('error', message='This Pi has no Wi-Fi.')
        return 1
    hotspot = portal = None
    connected = False
    wifi_was_off = not net.wifi_enabled()
    try:
        if wifi_was_off:
            net.set_wifi_enabled(True)
        networks = []
        for _ in range(3):
            try:
                networks = net.scan(rescan=True)
                if networks:
                    break
            except NetworkError as e:
                log.info('scan: %s', e)
            time.sleep(2)

        with open(DNSMASQ_CONF, 'w') as f:
            f.write('# Written by moonlight-wifi-setup while phone setup runs: every name points at the Pi,\n'
                    '# so phones open the setup page by themselves.\naddress=/#/%s\n' % HOTSPOT_IP)
        password = ''.join(secrets.choice(PASSWORD_CHARS) for _ in range(8))
        hotspot = Hotspot(net.wifi_dev, hotspot_ssid(), password)
        hotspot.start()
        requests = queue.Queue()
        portal = Portal(networks, requests)
        info = {'hotspot_ssid': hotspot.ssid, 'hotspot_password': password, 'url': URL}
        status.set('ready', last_error='', **info)
        deadline = time.monotonic() + IDLE_TIMEOUT

        while not stop.is_set():
            try:
                req = requests.get(timeout=1)
            except queue.Empty:
                if time.monotonic() > deadline:
                    status.set('stopped', message='Phone setup timed out after 15 minutes.')
                    break
                continue
            ssid = req['ssid']
            status.set('connecting', ssid=ssid)
            time.sleep(2)               # let the phone receive the "connecting" page
            hotspot.stop()
            try:
                if not req['hidden'] and not wait_for_ssid(net, ssid):
                    raise NetworkError("Couldn't find %s. Is it in range?" % ssid)
                net.connect_new(ssid, req['password'], req['security'], hidden=req['hidden'])
                connected = True
                status.set('connected', ssid=ssid)
                break
            except NetworkError as e:
                log.info('connect to %s failed: %s', ssid, e)
                portal.last_error = str(e)
                status.set('failed', message=str(e))
                hotspot.start()
                status.set('ready', last_error=str(e), **info)
                deadline = time.monotonic() + IDLE_TIMEOUT
        else:
            if not connected:
                status.set('stopped', message='Phone setup stopped.')
    except Exception as e:
        log.exception('phone setup failed')
        status.set('error', message=str(e))
        return 1
    finally:
        if portal:
            portal.close()
        if hotspot and not connected:
            hotspot.remove()
        elif hotspot:
            run(['nmcli', 'connection', 'delete', 'id', HOTSPOT_NAME])
        try:
            os.remove(DNSMASQ_CONF)
        except OSError:
            pass
        # Cancelled: leave Wi-Fi off again if it was off before (e.g. on Ethernet).
        if wifi_was_off and not connected:
            try:
                net.set_wifi_enabled(False)
            except NetworkError:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
