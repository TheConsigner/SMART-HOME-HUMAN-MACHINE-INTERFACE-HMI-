"""
G5 Smart Home HMI  -  main.py
ESP32 NodeMCU-32S  |  MicroPython

Upload to ESP32 root:
  main.py       <- this file
  index.html    <- dashboard HTML/CSS/JS
  hmi_pin.txt   <- auto-created on first PIN save (do not edit manually)

Pins:
  PIR      -> GPIO13   (motion sensor)
  Buzzer   -> GPIO19
  DHT22    -> GPIO14
  Bedroom  -> GPIO25
  Bathroom -> GPIO26
  Kitchen  -> GPIO27
  LivRoom  -> GPIO32
  Heater   -> GPIO23
  Fan      -> GPIO5
  Servo    -> GPIO18   (gate, 0=closed, 90=open)
  Relay    -> GPIO4    (main load)
  V_sense  -> GPIO33   (10k/1k divider -> ADC, ATTN_11DB)
  I_sense  -> GPIO34   (0.22ohm shunt, ATTN_0DB, 0-1.1V)
  Fire     -> GPIO35   (high = fire detected)
  LED_RED  -> GPIO2    (fire active indicator)
  LED_GRN  -> GPIO15   (safe / no fire)
  LED_BLU  -> GPIO21   (post-fire cooldown, 20 s)
  LED_WHT  -> GPIO22   (security light, manual toggle via HMI)
"""

import gc
import network
import socket
import time
import json
import os
import ubinascii
from machine import Pin, ADC, PWM
import dht

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
SSID            = "WINSTONXC35"
PASSWORD        = "nilexd35"
COST_PER_WH     = 10.0
ALARM_REARM_MS  = 10_000
FIRE_PUTOFF_MS  = 5_000
BLUE_HOLD_MS    = 20_000
HTML_FILE       = "index.html"
PIN_FILE        = "hmi_pin.txt"

# ─── PINS ─────────────────────────────────────────────────────────────────────
pir          = Pin(13, Pin.IN)
buzzer       = Pin(19, Pin.OUT, value=0)
sensor       = dht.DHT11(Pin(14))

led_bedroom  = Pin(25, Pin.OUT, value=0)
led_bathroom = Pin(26, Pin.OUT, value=0)
led_kitchen  = Pin(27, Pin.OUT, value=0)
led_living   = Pin(32, Pin.OUT, value=0)
ROOM_PINS    = {'bedroom': led_bedroom, 'bathroom': led_bathroom,
                'kitchen': led_kitchen,  'living':  led_living}

heater       = Pin(23, Pin.OUT, value=0)
fan_pin      = Pin(5,  Pin.OUT, value=0)
relay        = Pin(4,  Pin.OUT, value=0)

servo_pwm = PWM(Pin(18), freq=50)
def servo_angle(deg):
    servo_pwm.duty(int(26 + (deg / 180.0) * 97))
servo_angle(0)

adc_v = ADC(Pin(33)); adc_v.atten(ADC.ATTN_11DB); adc_v.width(ADC.WIDTH_12BIT)
adc_i = ADC(Pin(34)); adc_i.atten(ADC.ATTN_0DB);  adc_i.width(ADC.WIDTH_12BIT)

fire_pin = Pin(35, Pin.IN)
led_red  = Pin(2,  Pin.OUT, value=0)
led_grn  = Pin(15, Pin.OUT, value=0)
led_blu  = Pin(21, Pin.OUT, value=0)
led_wht  = Pin(22, Pin.OUT, value=0)
led_grn.value(1)   # boot: safe/green

# ─── SHARED STATE ─────────────────────────────────────────────────────────────
state = {
    "motion": False, "buzzer_on": False, "alarm_disarmed": False,
    "temp_c": 0.0, "temp_f": 32.0, "humidity": 0.0, "dht_ok": False,
    "rooms": {"bedroom": 0, "bathroom": 0, "kitchen": 0, "living": 0},
    "gate_open": False, "heater_on": False, "fan_on": False,
    "heat_setpoint": 18.0, "cool_setpoint": 28.0, "main_load": 0,
    "voltage": 0.0, "current": 0.0, "power_w": 0.0,
    "energy_wh": 0.0, "cost_ksh": 0.0,
    "uptime_s": 0, "ip": "0.0.0.0", "wifi_connected": False,
    "fire_state": "safe", "fire_ever": False, "white_light": 0,
}

_t0             = time.ticks_ms()
motion_last_ms  = 0
energy_last_ms  = _t0
dht_last_ms     = _t0
buzzer_until_ms = 0
disarm_until_ms = 0
fire_clear_ms   = 0
blue_until_ms   = 0
_prev_fire_raw  = 0

# ─── PIN AUTH  ────────────────────────────────────────────────────────────────
# Loaded inside main() AFTER boot to avoid compile-time heap pressure.
# hmi_pin.txt format:  pin,enabled   e.g.  1234,1   or  ,0
_pin = None    # str "0000".."9999" or None
_pen = False   # True = gate active
_tok = None    # current session token

def _pin_load():
    try:
        with open(PIN_FILE) as f:
            parts = f.read().strip().split(',')
        p  = parts[0] if parts[0] else None
        en = (parts[1] == '1') if len(parts) > 1 else False
        return p, en
    except:
        return None, False

def _pin_save():
    try:
        with open(PIN_FILE, 'w') as f:
            f.write('{},{}'.format(_pin if _pin else '', '1' if _pen else '0'))
    except Exception as e:
        print("pin save:", e)

def _new_tok():
    return ubinascii.hexlify(os.urandom(8)).decode()

# ─── WIFI ─────────────────────────────────────────────────────────────────────
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(SSID, PASSWORD)
        deadline = time.ticks_add(time.ticks_ms(), 12000)
        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("WiFi timeout - AP fallback")
                ap = network.WLAN(network.AP_IF)
                ap.active(True)
                ap.config(essid="G5-SmartHome", password="g5home123")
                state["ip"] = ap.ifconfig()[0]
                return
            time.sleep_ms(200)
    state["ip"]             = wlan.ifconfig()[0]
    state["wifi_connected"] = True
    print("WiFi OK  IP:", state["ip"])

# ─── ADC ──────────────────────────────────────────────────────────────────────
def avg_adc(adc, n=8):
    return sum(adc.read() for _ in range(n)) / n

# ─── FIRE STATE MACHINE ───────────────────────────────────────────────────────
def poll_fire(now_ms):
    global fire_clear_ms, blue_until_ms, _prev_fire_raw
    raw = fire_pin.value()
    if raw == 1:
        state["fire_ever"]  = True
        state["fire_state"] = "fire"
        fire_clear_ms = 0
        led_red.value(1); led_grn.value(0); led_blu.value(0)
    else:
        if _prev_fire_raw == 1:
            fire_clear_ms = now_ms
        if state["fire_state"] == "fire":
            if fire_clear_ms > 0 and time.ticks_diff(now_ms, fire_clear_ms) >= FIRE_PUTOFF_MS:
                state["fire_state"] = "putoff"
                blue_until_ms = time.ticks_add(now_ms, BLUE_HOLD_MS)
                led_red.value(0); led_blu.value(1); led_grn.value(0)
        elif state["fire_state"] == "putoff":
            if time.ticks_diff(now_ms, blue_until_ms) >= 0:
                state["fire_state"] = "cooldown"
        elif state["fire_state"] == "cooldown":
            led_blu.value(0); led_grn.value(1); led_red.value(0)
            state["fire_state"] = "safe"
        elif state["fire_state"] == "safe":
            if not state["fire_ever"]:
                led_grn.value(1); led_red.value(0); led_blu.value(0)
    _prev_fire_raw = raw

# ─── SENSOR POLL ──────────────────────────────────────────────────────────────
def poll_sensors():
    global motion_last_ms, energy_last_ms, dht_last_ms
    global buzzer_until_ms, disarm_until_ms
    now_ms = time.ticks_ms()
    state["uptime_s"] = now_ms // 1000

    if time.ticks_diff(now_ms, dht_last_ms) >= 3000:
        dht_last_ms = now_ms
        ok = False
        for _ in range(3):
            try:
                sensor.measure()
                rc = sensor.temperature(); rh = sensor.humidity()
                if -40.0 <= rc <= 80.0 and 0.0 <= rh <= 100.0:
                    state["temp_c"]   = round(rc, 1)
                    state["temp_f"]   = round(rc * 9.0 / 5.0 + 32.0, 1)
                    state["humidity"] = round(max(0.0, min(100.0, rh)), 1)
                    state["dht_ok"]   = True
                    ok = True; break
            except Exception:
                pass
            time.sleep_ms(80)
        if not ok:
            state["dht_ok"] = False

    if state["dht_ok"]:
        t = state["temp_c"]
        heater.value(1 if t < state["heat_setpoint"] else 0)
        state["heater_on"] = t < state["heat_setpoint"]
        fan_pin.value(1 if t > state["cool_setpoint"] else 0)
        state["fan_on"] = t > state["cool_setpoint"]

    disarmed = (time.ticks_diff(disarm_until_ms, now_ms) > 0)
    state["alarm_disarmed"] = disarmed
    if pir.value() == 1:
        motion_last_ms = now_ms; state["motion"] = True
        if not state["buzzer_on"] and not disarmed:
            state["buzzer_on"] = True
            buzzer_until_ms = time.ticks_add(now_ms, 2000)
            buzzer.value(1)
    else:
        if time.ticks_diff(now_ms, motion_last_ms) > 5000:
            state["motion"] = False
    if state["buzzer_on"] and time.ticks_diff(now_ms, buzzer_until_ms) >= 0:
        buzzer.value(0); state["buzzer_on"] = False

    poll_fire(now_ms)

    raw_v = avg_adc(adc_v); raw_i = avg_adc(adc_i)
    state["voltage"] = round(raw_v * 3.6 / 4095.0 * 11.0, 3)
    state["current"] = round(raw_i * 1.1 / 4095.0 / 0.22, 4)
    state["power_w"] = round(state["voltage"] * state["current"], 5)
    dt_ms = time.ticks_diff(now_ms, energy_last_ms)
    energy_last_ms = now_ms
    if state["main_load"] and dt_ms > 0:
        state["energy_wh"] += state["power_w"] * (dt_ms / 3_600_000.0)
    state["cost_ksh"] = round(state["energy_wh"] * COST_PER_WH, 6)

# ─── HTTP HELPERS ─────────────────────────────────────────────────────────────
def _send_json(client, obj):
    body = json.dumps(obj).encode()
    client.sendall(('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                    'Access-Control-Allow-Origin: *\r\n'
                    'Content-Length: {}\r\n\r\n').format(len(body)).encode())
    client.sendall(body)

def _send_401(client):
    client.sendall(b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n')

def _stream_html(client):
    """Stream index.html from flash in 1 KB chunks - never loads full file into RAM."""
    try:
        sz = os.stat(HTML_FILE)[6]
    except:
        client.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
        return
    client.sendall(('HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n'
                    'Content-Length: {}\r\n\r\n').format(sz).encode())
    with open(HTML_FILE, 'rb') as f:
        while True:
            chunk = f.read(1024)
            if not chunk:
                break
            client.sendall(chunk)
    gc.collect()

def _get_cookie(req, name):
    i = req.find('Cookie:')
    if i < 0: i = req.find('cookie:')
    if i < 0: return None
    e = req.find('\r\n', i)
    seg = req[i + 7: e if e >= 0 else None]
    for p in seg.split(';'):
        p = p.strip()
        eq = p.find('=')
        if eq > 0 and p[:eq].strip() == name:
            return p[eq + 1:].strip()
    return None

def url_param(qs, key):
    for part in qs.split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            if k == key:
                return v
    return None

# ─── REQUEST HANDLER ──────────────────────────────────────────────────────────
def _recv_request(client):
    # Bug-1 fix: old loop stopped at first chunk < 512 bytes, which is almost
    # always the very first read (headers fit in one TCP segment).
    # Now we read until we see the header terminator \r\n\r\n, capped at 4 KB.
    client.settimeout(3)
    data = b""
    try:
        while len(data) < 4096:
            try:
                chunk = client.recv(256)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
            if b'\r\n\r\n' in data:
                break
    except Exception:
        pass
    return data.decode('utf-8', 'ignore')

def handle_request(client):
    global disarm_until_ms, _pin, _pen, _tok
    try:
        req  = _recv_request(client)
        gc.collect()

        first = req.split('\r\n', 1)[0] if req else ''
        parts = first.split(' ')
        if len(parts) < 2: return
        path = parts[1]
        page, qs = path.split('?', 1) if '?' in path else (path, '')

        # ── PIN endpoints: always reachable, no session check ─────────────────

        if page == '/pin/status':
            # Browser checks this on load to decide whether to show login gate
            _send_json(client, {'set': _pin is not None, 'en': _pen})
            return

        if page == '/pin/login':
            # Browser submits entered PIN; on success we issue a session cookie
            entered = url_param(qs, 'p')
            if _pen and _pin and entered == _pin:
                _tok = _new_tok()
                body = json.dumps({'ok': True}).encode()
                client.sendall(('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                                 'Set-Cookie: g5t={};Path=/\r\n'
                                 'Content-Length: {}\r\n\r\n').format(_tok, len(body)).encode())
                client.sendall(body)
            else:
                _send_json(client, {'ok': False})
            return

        if page == '/pin/save':
            # Security tab calls this after any PIN set/change/remove/toggle
            p  = url_param(qs, 'p')
            en = url_param(qs, 'en')
            _pin = p if p else None
            if en is not None:
                _pen = (en == '1')
            if not _pen or _pin is None:
                _tok = None     # invalidate any live session
            _pin_save()         # persist to flash immediately
            _send_json(client, {'ok': True})
            return

        # ── Session gate: enforce when PIN is active ───────────────────────────
        if _pen and _pin:
            if _get_cookie(req, 'g5t') != _tok or _tok is None:
                _send_401(client)
                return

        # ── Normal routes (open when PIN inactive, or session valid) ──────────

        if page == '/cmd':
            gv = url_param(qs, 'gate')
            if gv is not None:
                state["gate_open"] = (gv == '1')
                servo_angle(90 if state["gate_open"] else 0)

            rm = url_param(qs, 'room'); rv = url_param(qs, 'val')
            if rm and rv:
                v = int(rv); state["rooms"][rm] = v
                ROOM_PINS.get(rm, led_bedroom).value(v)

            lv = url_param(qs, 'load')
            if lv is not None:
                state["main_load"] = int(lv); relay.value(state["main_load"])

            hs = url_param(qs, 'heat_sp'); cs = url_param(qs, 'cool_sp')
            if hs:
                try: state["heat_setpoint"] = float(hs)
                except: pass
            if cs:
                try: state["cool_setpoint"] = float(cs)
                except: pass

            if url_param(qs, 'disarm') == '1':
                buzzer.value(0); state["buzzer_on"] = False
                disarm_until_ms = time.ticks_add(time.ticks_ms(), ALARM_REARM_MS)
                state["alarm_disarmed"] = True

            wv = url_param(qs, 'white')
            if wv is not None:
                state["white_light"] = int(wv); led_wht.value(state["white_light"])

            poll_sensors()
            _send_json(client, state)

        elif page == '/state':
            poll_sensors()
            _send_json(client, state)

        else:
            _stream_html(client)   # serves index.html from flash, no RAM spike

    except Exception as e:
        print("Req err:", e)
    finally:
        try: client.close()
        except: pass
    gc.collect()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    global _pin, _pen

    print("=" * 46)
    print("  G5 Smart Home HMI  |  ESP32 NodeMCU-32S")
    print("=" * 46)
    print("DHT22 warm-up (2 s)...")
    time.sleep(2)
    gc.collect()

    connect_wifi()
    print("Navigate to: http://{}".format(state["ip"]))

    # Load PIN AFTER boot - hardware + WiFi are settled, heap pressure is low
    _pin, _pen = _pin_load()
    print("PIN: set={} enabled={}".format(_pin is not None, _pen))
    gc.collect()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', 80))
    srv.listen(5)          # slightly larger backlog
    # Bug-2 fix: use a very short timeout (50 ms) so we can drain the full
    # accept queue every loop iteration instead of handling only one client
    # and making the browser's next request (e.g. /pin/status) time out.
    srv.settimeout(0.05)
    print("Server ready.")

    poll_last = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        if time.ticks_diff(now, poll_last) >= 1000:
            poll_last = now
            try: poll_sensors()
            except Exception as e: print("Poll err:", e)
            gc.collect()

        # Drain ALL queued connections (up to 4) per loop tick so that a
        # second browser request fired immediately after the HTML load is
        # served before it times out.
        for _ in range(4):
            try:
                client, _ = srv.accept()
            except OSError:
                break          # backlog empty - exit inner loop
            except Exception as e:
                print("Accept err:", e)
                break
            try:
                handle_request(client)
            except Exception as e:
                print("Server err:", e)

main()
