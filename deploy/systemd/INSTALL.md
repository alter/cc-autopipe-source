# systemd install

cc-autopipe ships two unit files for `systemd` operation:

- `cc-autopipe.service` — the main orchestrator (Type=simple,
  Restart=always)
- `cc-autopipe-watchdog.service` — separate process pinging the
  orchestrator PID every 5 min and restarting it via
  `cc-autopipe start --foreground` if dead

Roman runs WSL2; systemd is opt-in there. See `deploy/WSL2.md` for
either enabling systemd in WSL or falling back to Windows Task
Scheduler.

## Install

```bash
sudo cp deploy/systemd/cc-autopipe.service /etc/systemd/system/
sudo cp deploy/systemd/cc-autopipe-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cc-autopipe cc-autopipe-watchdog
sudo systemctl start cc-autopipe
```

## Verify

```bash
sudo systemctl status cc-autopipe
journalctl -u cc-autopipe -f
sudo systemctl status cc-autopipe-watchdog
```

## Uninstall

```bash
sudo systemctl stop cc-autopipe-watchdog cc-autopipe
sudo systemctl disable cc-autopipe-watchdog cc-autopipe
sudo rm /etc/systemd/system/cc-autopipe.service
sudo rm /etc/systemd/system/cc-autopipe-watchdog.service
sudo systemctl daemon-reload
```

## Customising

If your engine path is not `/mnt/c/claude/artifacts/repos/cc-autopipe-source`,
edit `ExecStart` in both unit files before copying, or run a
quick `sed -i 's|/mnt/c/.*cc-autopipe-source|/your/path|g' deploy/systemd/*.service`.

The `User=` line assumes username `alter`; change to match your
WSL user (`whoami`).
