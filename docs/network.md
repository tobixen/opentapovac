# Tapo vacuum — reaching it from outside the house (plan, 2026-09-24)

*Historical.*  Since written, the isolated 2.4 GHz network is up and the
robot is on it (10.47.128.10); WireGuard is not done yet.  Kept for the
reasoning, which applies to any setup.

When written, nothing below had been applied.  The router changes were on branch
`wireguard-insecure-wlan` in the (private) router config repo;
the wifi key and the WireGuard keys there are placeholders, to be
generated and filled in on the router.  The robot notes come from the
app code and python-kasa, not from the robot.

## Two networks — check this first

* The robot is on wifi "old ISP wifi", LAN 192.168.1.0/24.
* The OpenWrt router (config in a private repo) is a different network:
  LAN 10.47.127.0/24, wifi "home.example.net" on 5 GHz only, WAN with
  its own public IP <public ip>.  So it is not behind the 192.168.1.x box
  and cannot reach the robot today.
* Consequence: once the robot has moved, a laptop at home on
  "old ISP wifi" can no longer reach it either.  At home the laptop
  must be on the OpenWrt wifi (or use the tunnel even at home).

Laptop access is for testing only, from one laptop.

**Web control through the cloud: not from TP-Link.**  TP-Link's pages
only mention the Tapo app and voice assistants for the robot vacuums; there
is no browser portal.  The cloud route the app uses (the "thing cloud"
pass-through) is not public.  A web page would have to be our own,
e.g. Home Assistant with the RV50 integration, running at home and
reached over WireGuard — the same path as the laptop.

## Router: a 2.4 GHz network for the robot

The 5 GHz network keeps its SSID, `home.example.net`, and WPA3.  The
2.4 GHz radio (`radio0`, disabled until now) becomes
**`iot.example.net`**:

* **WPA2 only (`psk2+ccmp`), not `sae` or `sae-mixed`.**  IoT wifi chips
  often fail on WPA3 and on WPA2/WPA3 transition mode (PMF).  WPA2
  matches python-kasa's `wifi join` default, `wpa2_psk`.
* Client isolation (`isolate`), its own network 10.47.128.0/24 and its own
  firewall zone `insecure`: no access to the LAN or the router except
  DHCP and DNS.  Into the zone, only the robot's tcp/4433 is open,
  from the LAN and from WireGuard.
* Static lease: the robot gets 10.47.128.10.

### Closing the egress

Internet access for the zone is the firewall rule `insecure_egress`.
It logs each new connection, and the zone logs everything it rejects
(10/minute), so `logread` shows what the robot tries to reach either
way.  To switch it on or off, e.g. just for a firmware update:

```
uci set firewall.insecure_egress.enabled=0   # 1 = open
service firewall reload
```

Without `uci commit` this is a runtime-only toggle: `/etc/config/firewall`
is untouched, a reboot restores the committed state, and `uci revert
firewall; service firewall reload` switches it back early.  DNS stays
allowed from the zone even when egress is closed, which leaves a DNS
exfiltration channel; accepted.  The system log is a 128 KB ring buffer
(`log_size` in `system`), so connection logs will push other lines out
fast.  Raise it, or send the log to another box with `log_ip`.

Switching the rule off cuts the robot off from the cloud.  What that
costs:

* The phone app only works when the phone can reach the robot directly;
  away from home it stops working.  Whether the app even talks locally
  to a robot on another subnet is unknown — the app finds devices by
  broadcast.
* No firmware updates, no cloud map backup (`mapRecovery` pulls from the
  cloud), no voice-language changes, no "find my robot".
* Time: the robot presumably uses NTP.  Without it, schedules may drift
  or stop.  Fix: `enable_server '1'` in `system` and allow udp/123 from
  `insecure` to the router.
* DNS names the robot looks up are not logged; only IP addresses are.
  dnsmasq's `logqueries` would log them, but for the whole house.
* Local control should keep working: TPAP's SPAKE2+ handshake runs
  against the robot itself, with credentials stored on the robot.
  **Unverified** — test by removing the forwarding for a day and
  running the laptop commands over WireGuard.

## Router: WireGuard instead of opening the robot to the internet

* `wg0` on the router, with one UDP port open from wan (e.g. 51820).
  WireGuard does not answer packets without a valid key, so the open port
  reveals nothing.
* The laptop is a peer (10.47.129.2).  The `wg` zone may reach only the
  robot's tcp/4433.
* Needs `wireguard-tools` (plus `luci-proto-wireguard` for the web UI);
  check with `apk list -I | grep wireguard` / `opkg list-installed`.
* The robot is then never reachable from the internet directly, only by
  laptops that hold a key.
* Check that other VPNs on the laptop don't route 10.47.127.0/24.

## Robot side: switching wifi — what the code says

* The robot remembers the networks it has been on:
  `getWirelessConnectedList`, `getWirelessConnectedStatus` (fields
  `key_type`, `signal_level`, `last_fail_reason`), and
  `delWirelessConnectedItem {"ssid"}`.  The app lists them in the robot's
  settings.
* The app has **no "add network" call** for the robot.  A new network goes
  through the generic Tapo quick setup, `set_qs_info` with
  `wireless: {ssid, password, key_type}` (base64), over the robot's own
  AP or over BLE.
* python-kasa has this built in: `kasa wifi scan` (`get_wireless_scan_info`)
  and `kasa wifi join SSID --password …` (`set_qs_info`, key type
  `wpa2_psk`).  Its docstring says the device falls back to the previous
  network if joining fails.  **Untested on this robot, and unknown whether
  it accepts `set_qs_info` over TPAP once it is already set up.**
* Worst case the robot drops off wifi.  Recovery is the app's normal
  re-setup (BLE, button on the robot).  Map, rooms and settings live on
  the robot and in the cloud backup, so they should survive.  Take a map
  backup in the app first anyway.

### Pre-checks, all read-only, once back on the home LAN

1. `kasa … command getWirelessConnectedList` and
   `getWirelessConnectedStatus`: which networks it remembers, the current
   key type and signal.
2. After the 2.4 GHz network is up: `kasa … wifi scan`, or
   `command getWirelessScanInfo`.  Does the robot see the new SSID, with
   what signal, and with which `key_type`/`cipher_type`?
3. `component_nego`: look for `quick_setup` / `wireless` components and
   their versions.
4. Then decide between `kasa wifi join` from the laptop (quick, but
   untested) and "change Wi-Fi" / re-setup in the app (known to work).
   Either way, with someone at home.

## Order

1. Router: check out branch `wireguard-insecure-wlan` on the router, fill
   in the keys, `wifi reload` / `service network restart` /
   `service firewall restart`.
2. Robot pre-checks 1–3.
3. Move the robot to the new SSID; update the IP in
   `field-notes.md`.
4. WireGuard on the router, laptop as peer; test from outside.
5. Read the egress log for a while to see what the robot talks to, then
   try a day with `insecure_egress` off.
