# Running cc-autopipe on WSL2

WSL2 systemd is opt-in. cc-autopipe ships systemd unit files in
`deploy/systemd/` for the orchestrator and watchdog, but Roman's
default WSL2 install may not have systemd active. Two paths:

## Path A — Enable systemd in WSL (recommended)

```bash
echo -e '[boot]\nsystemd=true' | sudo tee /etc/wsl.conf
```

Then, in **Windows PowerShell** (not the WSL shell):

```powershell
wsl --shutdown
```

Reopen your WSL terminal. Verify:

```bash
systemctl --user --version   # should print version
cc-autopipe doctor | grep wsl-systemd   # should be OK
```

Then proceed with the standard install:

```bash
sudo cp deploy/systemd/cc-autopipe.service /etc/systemd/system/
sudo cp deploy/systemd/cc-autopipe-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cc-autopipe cc-autopipe-watchdog
sudo systemctl start cc-autopipe
journalctl -u cc-autopipe -f
```

## Path B — Windows Task Scheduler fallback

If you can't enable WSL2 systemd (corporate policy, hesitation about
restarting the distro mid-task, etc.), drive the orchestrator from
Windows Task Scheduler.

### One-time setup

Save the following as `cc-autopipe.xml` somewhere on the Windows side:

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>cc-autopipe orchestrator (via WSL)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Settings>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wsl.exe</Command>
      <Arguments>-d Ubuntu -e bash -c "cd /home/alter &amp;&amp; /mnt/c/claude/artifacts/repos/cc-autopipe-source/src/helpers/cc-autopipe start --foreground &gt;&gt; /home/alter/.cc-autopipe/log/wsl-task.log 2&gt;&amp;1"</Arguments>
    </Exec>
  </Actions>
</Task>
```

Adjust the distro name (`-d Ubuntu`) and engine path to your install.

Import via PowerShell:

```powershell
schtasks /Create /XML cc-autopipe.xml /TN "cc-autopipe"
```

Start it now (or wait for next logon):

```powershell
schtasks /Run /TN "cc-autopipe"
```

Watch the log from inside WSL:

```bash
tail -f ~/.cc-autopipe/log/wsl-task.log
```

### Watchdog under Task Scheduler

For a Path B setup, the watchdog also needs to be a Task Scheduler
entry. Save `cc-autopipe-watchdog.xml`:

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>cc-autopipe watchdog (via WSL)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT2M</Delay>
    </LogonTrigger>
  </Triggers>
  <Settings>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wsl.exe</Command>
      <Arguments>-d Ubuntu -e bash -c "/usr/bin/python3 /mnt/c/claude/artifacts/repos/cc-autopipe-source/src/watchdog/watchdog.py &gt;&gt; /home/alter/.cc-autopipe/log/wsl-watchdog-task.log 2&gt;&amp;1"</Arguments>
    </Exec>
  </Actions>
</Task>
```

```powershell
schtasks /Create /XML cc-autopipe-watchdog.xml /TN "cc-autopipe-watchdog"
```

The 2-minute delay on the watchdog gives the orchestrator a head start
on first logon, so the watchdog doesn't restart-loop while the
orchestrator is still bootstrapping.

## Path comparison

| Aspect              | Path A (systemd) | Path B (Task Scheduler) |
|---------------------|------------------|-------------------------|
| Auto-start on boot  | yes              | requires Windows logon  |
| Survives WSL crash  | systemd restarts | Task Scheduler restarts |
| Logs                | `journalctl`     | log file in `~/.cc-autopipe/log/wsl-task.log` |
| Setup friction      | one-time WSL restart | Windows-side XML import |
| Keychain prompts    | none             | none (orchestrator does not interact) |

Both paths are equivalent operationally. Path A is preferred when
WSL2 systemd is already in use.

## Verifying

After either path, in a WSL shell:

```bash
cc-autopipe status
tail -f ~/.cc-autopipe/log/aggregate.jsonl | jq -c '{ts, project, event}'
```

`cc-autopipe doctor` will report `wsl-systemd: ok` on Path A and
`wsl-systemd: fail` (with the Path B remediation hint) on Path B.
