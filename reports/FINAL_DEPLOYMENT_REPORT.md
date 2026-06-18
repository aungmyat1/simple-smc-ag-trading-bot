# Final Tailscale Exit Node Deployment Report
Generated: 2026-06-18
VPS: auto-trade-vps (GCP asia-southeast1-b)

---

## Summary

**Tailscale was already installed and configured as an exit node before this audit.**
No installation or configuration changes were required.

---

## Status Dashboard

| Item | Value | Status |
|------|-------|--------|
| Tailscale version | (see below) | ✅ |
| VPS public IP | 35.247.178.143 | ✅ |
| Tailscale IP (IPv4) | 100.106.165.119 | ✅ |
| Tailscale IP (IPv6) | fd7a:115c:a1e0::9636:a577 | ✅ |
| Exit node advertised | YES — "offers exit node" | ✅ |
| IPv4 forwarding | ENABLED (= 1) | ✅ |
| IPv6 forwarding | ENABLED (= 1) | ✅ |
| Firewall (UFW) | INACTIVE (GCP VPC handles perimeter) | ✅ |
| SSH accessible | YES (port 22, ens4) | ✅ |
| Trading bot (smc-bot) | RUNNING — 16h uptime | ✅ |
| DERP nearest | Singapore 1.6ms | ✅ |
| Direct UDP | YES | ✅ |

---

## Verify Tailscale version

```bash
tailscale version
```

---

## Test Results

| Test | Result |
|------|--------|
| A — VPS local IP | PASS (35.247.178.143) |
| B — Remote client via exit node | **PENDING** — requires user action on client device |

### To complete Test B:
```bash
# On your laptop/phone:
sudo tailscale up --exit-node=100.106.165.119
curl ifconfig.me   # must return 35.247.178.143

# When done, restore normal routing:
sudo tailscale up --exit-node=
```

If the admin console hasn't approved the exit node yet:
- Go to tailscale.com/admin/machines → auto-trade-vps → Edit route settings → enable exit node

---

## Rollback Procedure Summary

Full rollback steps are in [~/ROLLBACK_TAILSCALE.md](~/ROLLBACK_TAILSCALE.md).

Quick rollback (remove exit node only):
```bash
sudo tailscale set --advertise-exit-node=false
```

Full removal:
```bash
sudo tailscale down && sudo systemctl stop tailscaled && sudo apt-get remove --purge tailscale -y
```

---

## ⚠️ Critical Issues Found

### 1. DISK SPACE at 92% — ACTION REQUIRED
```
/dev/root  38G  35G  3.4G  92%  /
```
Only 3.4 GiB free. A disk-full condition will kill the trading bot and all services.

**Immediate investigation:**
```bash
du -sh /* 2>/dev/null | sort -rh | head -20
du -sh ~/*/  # find largest directories
docker system prune -f   # clean unused Docker layers (safe, no running containers)
journalctl --vacuum-size=500M  # trim old logs
```

### 2. Port 8000 publicly exposed on 0.0.0.0
The ag-auto-trade dashboard (`uvicorn`) is bound to all interfaces. Verify GCP VPC firewall
blocks external access or restrict the bind:
```bash
# Check if reachable externally:
curl -m 3 http://35.247.178.143:8000/  # from outside
# If open, add GCP firewall rule to block :8000 from internet, OR restrict uvicorn to 127.0.0.1
```

---

## Security Recommendations (Phase 9 — no auto-apply)

| Recommendation | Priority | Already done? |
|---------------|----------|--------------|
| fail2ban for SSH | HIGH | ✅ Already running |
| unattended-upgrades | HIGH | ✅ Already running |
| Restrict dashboard port 8000 to localhost or Tailscale IP only | HIGH | ⚠️ Needs check |
| Free disk space before it hits 95% | CRITICAL | ❌ Action needed |
| SSH key-only auth (disable passwords) | MEDIUM | Unknown |
| GCP VPC firewall audit | MEDIUM | Cannot inspect from inside |
| UFW activation (belt + suspenders) | LOW | Optional — GCP handles perimeter |
| Tailscale ACL: restrict which devices can use this exit node | LOW | Optional |

---

## Trading System Impact Assessment

Tailscale operates on its own virtual interface (`tailscale0`). It does not:
- Intercept traffic on `ens4` (Bybit/Pionex exchange connections)
- Add routing overhead to non-Tailscale traffic
- Affect `smc-bot.service` (confirmed still running at 16h uptime)

No latency increase observed on exchange connectivity. Trading systems unaffected.
