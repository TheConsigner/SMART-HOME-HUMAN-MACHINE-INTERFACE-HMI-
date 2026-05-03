# G5 Smart Home HMI
### ESP32 NodeMCU-32S Residential Automation Controller

A self-contained smart home controller running MicroPython on the ESP32 NodeMCU-32S. The device hosts its own web server and serves a full-featured browser dashboard from internal flash. No cloud, no app, no internet required — just connect to the same WiFi network and open a browser.

## Features
•	Real-time temperature and humidity (DHT11)
•	Motion detection alarm with auto-rearm (PIR HC-SR501)
•	Fire detection state machine with RGB LED indicators
•	Servo-controlled gate with HMI open/close buttons
•	Four-room LED lighting control
•	Automatic HVAC: heater and fan controlled by setpoints
•	Main load relay (fridge / appliance)
•	White security light toggle
•	Power monitoring: voltage, current, power, energy (Wh), cost (KSH)
•	4-digit PIN security — persisted to flash, survives reboot
•	Vibrant colour-coded dashboard with fire overlay and motion banner

## Files
•	main.py — MicroPython firmware (sensor polling, HTTP server, PIN auth)
•	index.html — Single-page dashboard (HTML/CSS/JS, served from ESP32 flash)
•	hmi_pin.txt — PIN config (auto-generated; format: pin,enabled e.g. 1234,1)

## Hardware Requirements
•	ESP32 NodeMCU-32S
•	DHT11 temperature/humidity sensor
•	PIR motion sensor (HC-SR501)
•	Flame/fire sensor module
•	SG90/MG90S servo motor
•	5V single-channel relay module
•	Passive buzzer
•	LEDs: Red (GPIO2), Green (GPIO15), Blue (GPIO21), White (GPIO22), 4x room LEDs
•	12V incandescent bulbs (300 mA) for heater and main load
•	12V DC fan
•	14V DC battery + 5V buck converter
•	0.22Ω shunt resistor and 10kΩ/1kΩ voltage divider for ADC
•	NPN transistors, resistors, capacitors, perfboard

## Quick Start
•	1. Flash MicroPython firmware to ESP32 using esptool.py
•	2. Open Thonny IDE, connect to ESP32 (MicroPython interpreter)
•	3. Upload main.py, index.html, and hmi_pin.txt to the ESP32 flash root
•	4. Set your WiFi SSID and password in main.py (SSID and PASSWORD constants)
•	5. Press EN (reset) on the ESP32 — watch the Thonny shell for the IP address
•	6. Open a browser and navigate to http://<ESP32_IP>
•	7. Once uploaded, the PC is no longer needed — power from battery

## PIN Security
Navigate to the System Security tab in the dashboard. Use the keypad to set a 4-digit PIN. Toggle the switch to enable. When enabled, any browser visiting the IP must enter the PIN before accessing the dashboard. The PIN survives reboots (stored in hmi_pin.txt on flash). Factory reset: set hmi_pin.txt contents to ,0

## License
Business Source License 1.1 (BUSL-1.1) — source available, non-production use permitted. Production use requires a commercial licence from the author. See LICENSE file for full terms. Copyright © 2026 Abraham Winston Ogoch.
