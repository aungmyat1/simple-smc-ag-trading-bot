# Connectivity Test Report
Generated: 2026-06-18

## Test A — VPS Local IP (PASS)

```
$ curl -s ifconfig.me
35.247.178.143
```

Result: **PASS** — VPS public IP confirmed as 35.247.178.143

## Test B — Remote Client via Exit Node

**Status: PENDING USER ACTION**

Tailscale exit node is advertised but Test B requires a client device to activate it:

### Steps to enable exit node on client:

**Mac/Linux:**
```bash
sudo tailscale up --exit-node=100.106.165.119
curl ifconfig.me   # should return 35.247.178.143
```

**Windows (Tailscale GUI):**
1. Open Tailscale tray icon
2. Click "Use exit node" → select "auto-trade-vps"
3. Open browser → `ifconfig.me` → should show `35.247.178.143`

**Admin console (required if not auto-approved):**
1. Go to `tailscale.com/admin/machines`
2. Find `auto-trade-vps`
3. Click `. . .` → "Edit route settings" → enable "Use as exit node"

### Expected result:
```
Remote curl ifconfig.me → 35.247.178.143  ✓ PASS
```

## Tailscale netcheck (from VPS)

```
UDP: true
IPv4: yes, 35.247.178.143:36717
IPv6: no (OS has support, but no public IPv6 on GCP interface)
MappingVariesByDestIP: false
CaptivePortal: false
Nearest DERP: Singapore (1.6ms)
```

Connectivity quality: **EXCELLENT** — direct UDP, Singapore DERP at 1.6ms.
