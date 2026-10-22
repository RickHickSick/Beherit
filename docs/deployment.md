# Beherit Deployment Guide 🚀

Beherit is designed to run anywhere: on local desktops, VPS instances, or home servers (like Raspberry Pi 5).

---

## 1. Quick Local / Bare-Metal Deployment

### Installation
```bash
git clone https://github.com/RickHickSick/Beherit.git
cd Beherit

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configuration
```bash
cp .env.example .env
# Adjust BEHERIT_PORT or BEHERIT_AGENTS_DIR if using an Obsidian vault
```

### Start Server
```bash
uvicorn beherit.main:app --host 0.0.0.0 --port 8650 --reload
```
Open `http://localhost:8650` in your browser.

---

## 2. Docker & Docker Compose Deployment

### Start with Docker Compose
```bash
cp .env.example .env
docker compose up -d --build
```

### Verify Container
```bash
docker compose logs -f
```

---

## 3. Systemd Service Deployment (24/7 on Linux / Raspberry Pi)

1. Copy the systemd template:
   ```bash
   sudo cp systemd/beherit.service.template /etc/systemd/system/beherit.service
   ```
2. Replace `%USER%` and `%BEHERIT_DIR%` with your paths (e.g. `archibald` and `/home/archibald/Projects/Beherit`).
3. Reload systemd daemon and enable service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now beherit.service
   sudo systemctl status beherit.service
   ```

---

## 4. Tailscale & Remote Access (Safari iOS PWA)

* If running in a Tailscale mesh network, Beherit listens on `0.0.0.0:8650`.
* Simply open your machine's Tailscale IP or MagicDNS in Safari: `http://<your-tailscale-host>:8650`.
* In Safari iOS: tap the **Share** button and select **"Add to Home Screen"** to install as a standalone PWA app.

---

## 5. Homelab Dashboard (Homepage Integration)

To add Beherit to your Homepage dashboard (`services.yaml`), add:

```yaml
- AI & Automation:
    - Beherit:
        icon: si-robot
        href: http://<your-server-ip>:8650
        description: Autonomous AI Agent Platform
        widget:
          type: customapi
          url: http://<your-server-ip>:8650/health
          mappings:
            - field: status
              label: Status
```
