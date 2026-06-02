import os
import subprocess
import json

def get_ps():
    proc = subprocess.run(["ps", "-Ao", "pid=,command="], capture_output=True, text=True)
    return proc.stdout.splitlines()

def get_ps_ww():
    proc = subprocess.run(["ps", "-Ao", "pid=,command=", "-ww"], capture_output=True, text=True)
    return proc.stdout.splitlines()

ps1 = get_ps()
ps2 = get_ps_ww()

for line in ps1:
    if "Google Chrome" in line or "Chromium" in line:
        print("PS1 Chrome:", line[:200])
for line in ps2:
    if "Google Chrome" in line or "Chromium" in line:
        print("PS2 Chrome:", line[:200])

