%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-logs
Version:        0.1.0
Release:        21%{?dist}
Summary:        Vexor Logs server-side glue (API plugin + alert evaluator)
License:        Apache-2.0
URL:            https://github.com/sayonarase/vexor-logs
BuildArch:      noarch
Source0:        vexor-logs-api.tar.gz
Source1:        logs.env.example
Source2:        log-alerts-evaluator.service
Source3:        vexor-logs-filters.tar.gz
Source4:        install-linux-agent.sh
Source5:        install-windows-agent.ps1
Source6:        install-linux-agent-interactive.sh
Source7:        install-windows-agent-interactive.ps1
Source8:        vexor-cpanm-install
Source9:        vexor-plugin-deps.sudoers
Source10:       91-vexor-victorialogs.rules
Source11:       002_post_v0.1.0-5_schema_drift.sql
Source12:       vexor-logs-postinstall.sh
Source13:       003_log_checks.sql
Source14:       004_log_retention_overrides.sql
Source15:       005_log_alert_events.sql
Source16:       vexor-logs-retention-enforcer
Source17:       vexor-logs-retention-enforcer.service
Source18:       vexor-logs-retention-enforcer.timer

Requires:       vexor-victorialogs
Requires:       vexor-api >= 0.1.0-6
Requires:       python3

%description
Vexor Logs glue package. Ships the FastAPI plugin (logs router, log-alerts
CRUD, settings, saved searches, filter library, shipper deploy) into
vexor-api's plugin directory, installs the alert-evaluator systemd service,
provides the default /etc/vexor/logs.env file, the curated filter library
under /etc/vexor/logs/filters/, and the Linux + Windows install scripts.

%prep
%setup -q -c -T
tar xzf %{SOURCE0}
tar xzf %{SOURCE3}

%install
install -d %{buildroot}/opt/vexor/api/plugins/logs
cp -a vexor_logs_api/. %{buildroot}/opt/vexor/api/plugins/logs/

install -d %{buildroot}/etc/vexor/logs
install -m 0644 %{SOURCE1} %{buildroot}/etc/vexor/logs/logs.env.example

install -d %{buildroot}/etc/vexor/logs/filters
cp -a filters/. %{buildroot}/etc/vexor/logs/filters/

install -d %{buildroot}/usr/lib/systemd/system
install -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/vexor-log-alerts-evaluator.service

install -d %{buildroot}/opt/vexor/api/plugins/logs/install-scripts
install -m 0755 %{SOURCE4} %{buildroot}/opt/vexor/api/plugins/logs/install-scripts/install-linux-agent.sh
install -m 0644 %{SOURCE5} %{buildroot}/opt/vexor/api/plugins/logs/install-scripts/install-windows-agent.ps1
install -m 0755 %{SOURCE6} %{buildroot}/opt/vexor/api/plugins/logs/install-scripts/install-linux-agent-interactive.sh
install -m 0644 %{SOURCE7} %{buildroot}/opt/vexor/api/plugins/logs/install-scripts/install-windows-agent-interactive.ps1

install -d %{buildroot}/usr/bin
cat > %{buildroot}/usr/bin/vexor-log-alerts-evaluator <<'EOS'
#!/usr/bin/env bash
export PYTHONPATH=/opt/vexor/api:/opt/vexor/api/plugins${PYTHONPATH:+:$PYTHONPATH}
cd /opt/vexor/api
set -a; [ -f /etc/vexor/db.env ] && . /etc/vexor/db.env; [ -f /etc/vexor/logs.env ] && . /etc/vexor/logs.env; set +a
exec /opt/vexor/api/venv/bin/python -m logs.evaluator "$@"
EOS
chmod 0755 %{buildroot}/usr/bin/vexor-log-alerts-evaluator

install -Dpm 0755 %{SOURCE8}  %{buildroot}/usr/local/sbin/vexor-cpanm-install
install -Dpm 0440 %{SOURCE9}  %{buildroot}/etc/sudoers.d/vexor-plugin-deps
install -Dpm 0644 %{SOURCE10} %{buildroot}/etc/polkit-1/rules.d/91-vexor-victorialogs.rules
install -Dpm 0644 %{SOURCE11} %{buildroot}/usr/share/vexor-logs/migrations/002_post_v0.1.0-5_schema_drift.sql
install -Dpm 0644 %{SOURCE13} %{buildroot}/usr/share/vexor-logs/migrations/003_log_checks.sql
install -Dpm 0644 %{SOURCE14} %{buildroot}/usr/share/vexor-logs/migrations/004_log_retention_overrides.sql
install -Dpm 0644 %{SOURCE15} %{buildroot}/usr/share/vexor-logs/migrations/005_log_alert_events.sql
install -Dpm 0755 %{SOURCE16} %{buildroot}/usr/bin/vexor-logs-retention-enforcer
install -Dpm 0644 %{SOURCE17} %{buildroot}/usr/lib/systemd/system/vexor-logs-retention-enforcer.service
install -Dpm 0644 %{SOURCE18} %{buildroot}/usr/lib/systemd/system/vexor-logs-retention-enforcer.timer
install -Dpm 0755 %{SOURCE12} %{buildroot}/usr/share/vexor-logs/vexor-logs-postinstall.sh

%post
%systemd_post vexor-log-alerts-evaluator.service
if [ ! -f /etc/vexor/logs.env ]; then
    install -d /etc/vexor
    cp /etc/vexor/logs/logs.env.example /etc/vexor/logs.env 2>/dev/null || \
        echo 'VEXOR_LOGS_URL=http://127.0.0.1:9428' > /etc/vexor/logs.env
fi
# ensure retention key exists in /etc/vexor/logs.env
if ! grep -q '^VEXOR_LOGS_RETENTION_DAYS=' /etc/vexor/logs.env 2>/dev/null; then
    echo 'VEXOR_LOGS_RETENTION_DAYS=90' >> /etc/vexor/logs.env
fi
# Make sure vexor-api can import the plugin
if [ -d /opt/vexor/api/venv/lib ]; then
    SP=$(find /opt/vexor/api/venv/lib -maxdepth 3 -name site-packages -type d | head -1)
    if [ -n "$SP" ] && [ ! -e "$SP/vexor_logs_api" ]; then
        ln -sf /opt/vexor/api/plugins/logs/vexor_logs_api "$SP/vexor_logs_api"
    fi
fi
# Reload polkit so bundled rule takes effect (v0.1.0-7)
systemctl reload polkit 2>/dev/null || systemctl restart polkit 2>/dev/null || :

%systemd_post vexor-logs-retention-enforcer.timer
# Ensure new logs.env knobs exist (retention disk + syslog receiver)
for kv in 'VEXOR_LOGS_DISK_MODE=none' 'VEXOR_LOGS_DISK_BYTES=' 'VEXOR_LOGS_DISK_PERCENT=' 'VEXOR_LOGS_SYSLOG_UDP=' 'VEXOR_LOGS_SYSLOG_TCP='; do
    k=${kv%%=*}
    grep -q "^${k}=" /etc/vexor/logs.env 2>/dev/null || echo "$kv" >> /etc/vexor/logs.env
done
chown root:vexor /etc/vexor/logs.env 2>/dev/null || :
chmod 0660 /etc/vexor/logs.env 2>/dev/null || :
systemctl enable --now vexor-logs-retention-enforcer.timer 2>/dev/null || :
# Apply DB schema drift migrations - idempotent (v0.1.0-7)
if [ -x /usr/share/vexor-logs/vexor-logs-postinstall.sh ]; then
    /usr/share/vexor-logs/vexor-logs-postinstall.sh || :
fi

systemctl try-restart vexor-api.service 2>/dev/null || :

%preun
%systemd_preun vexor-log-alerts-evaluator.service
%systemd_preun vexor-logs-retention-enforcer.timer

%postun
%systemd_postun_with_restart vexor-log-alerts-evaluator.service
%systemd_postun vexor-logs-retention-enforcer.timer

%files
/opt/vexor/api/plugins/logs
%dir /etc/vexor/logs
/etc/vexor/logs/logs.env.example
%dir /etc/vexor/logs/filters
%config(noreplace) /etc/vexor/logs/filters/*.json
/usr/lib/systemd/system/vexor-log-alerts-evaluator.service
/usr/bin/vexor-log-alerts-evaluator

/usr/local/sbin/vexor-cpanm-install
%attr(0440,root,root) /etc/sudoers.d/vexor-plugin-deps
/etc/polkit-1/rules.d/91-vexor-victorialogs.rules
%dir /usr/share/vexor-logs
%dir /usr/share/vexor-logs/migrations
/usr/share/vexor-logs/migrations/002_post_v0.1.0-5_schema_drift.sql
/usr/share/vexor-logs/migrations/003_log_checks.sql
/usr/share/vexor-logs/migrations/004_log_retention_overrides.sql
/usr/share/vexor-logs/migrations/005_log_alert_events.sql
/usr/bin/vexor-logs-retention-enforcer
/usr/lib/systemd/system/vexor-logs-retention-enforcer.service
/usr/lib/systemd/system/vexor-logs-retention-enforcer.timer
/usr/share/vexor-logs/vexor-logs-postinstall.sh

%changelog
* Sat Jul 04 2026 Vexor <build@vexormon.com> - 0.1.0-21
- Log shippers: new admin-only GET /api/v1/logs/ingest-token so the GUI can
  show the ingest token and embed it in the install commands (shippers must
  send Authorization: Bearer <token> to /api/v1/logs/push or get 401).
- WinRM deploy command now uses `curl.exe -k` (works against a self-signed
  Vexor cert on Windows PowerShell 5.1) instead of Invoke-WebRequest, and no
  longer contains a stray backslash line-continuation that broke when pasted.
* Wed Jun 24 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-20
- New endpoint POST /api/v1/logs/ai-analyze: AI-assisted SRE triage of a log
  query using the system-wide LLM provider configured in vexor-api (operator+).

* Wed Jun 24 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-19
- evaluator: fix direct log-alert notifications. The unbound-rule notify
  path posted to a dead endpoint (http://127.0.0.1:8000/v1/notifications/
  dispatch) with an outdated payload and no auth, so those notifications
  silently failed. Now posts a DispatchEvent to
  http://127.0.0.1:8080/api/v1/notify/dispatch-internal authenticated with
  the /etc/vexor/notify-token file token, and direct dispatch is gated to
  rules without a Naemon host_binding (bound rules notify via Naemon's
  passive-check pipeline) to avoid double-notifying.

* Mon Jun 22 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-18
- API: new GET /api/v1/logs/shippers endpoint returning every host shipping logs
  with a last-seen freshness status (ok/stale/silent), powering the new Log
  Shippers overview in the UI.

* Sun Jun 21 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-17
- Filter library: add 9 ready-made filters for Microsoft SQL Server 2014+ and
  Always On Availability Groups (high-severity errors, login failures,
  deadlocks, I/O/corruption, backup/restore failures, memory pressure; AG role
  change/failover, not-synchronizing, connectivity/lease loss).

* Sun Jun 21 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-16
- Filter library: add 10 ready-made filters for Progress OpenEdge 11/12 and
  Apache Tomcat (PASOE agent/server errors, classic AppServer/WebSpeed broker
  failures, agent/server died, AdminServer, NameServer, DB .lg connection
  errors; Tomcat engine errors, startup/OOM failures, access-log 5xx).

* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-15
- Filter library: add 20 ready-made log filters for common Linux and Windows
  servers (disk full, filesystem/disk I/O errors, kernel panic, hardware/MCE,
  failed systemd units, MariaDB/MySQL errors, SELinux denials, fail2ban bans,
  time sync loss, NFS stalls, root SSH login; Windows service/app crashes,
  unexpected shutdown, disk/NTFS errors, account lockout, Defender detections,
  failed logons, bugcheck, time service). Linux filters match on message text so
  they work whether logs arrive via journald or files. Dropped the broken
  windows-event-critical preset (its source_type match never fired).
- Windows agent: deployed Vector now labels logs with the Vexor host name
  (new -HostName parameter, mirrors the Linux agent) and always populates _msg,
  so Windows logs show under the right host and the windows-* filters match.

* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-14
- Fix deployed Vector agents: skip TLS verification for the (commonly
  self-signed) Vexor ingest endpoint via tls.verify_certificate, and map the
  log message into _msg plus a service tag - previously remote logs arrived with
  no message body ("missing _msg field") and the agent failed config validation.

* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-13
- Deployed log agents now tag their logs with the Vexor host name. The deploy
  passes --host-name and the installer bakes it into the Vector/fluent-bit
  config (falling back to the box hostname), so logs show up under the host as
  Vexor knows it (e.g. an IP-named host) instead of the machine own hostname.

* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-12
- Log-shipper deploy now runs as a streamed background job (returns job_id);
  the UI shows live install output instead of an endless spinner. Temp SSH
  keys are cleaned up when the job finishes.
- Add ready-made log-alert filters for nginx, Apache/httpd, Caddy and Progress
  OpenEdge (.lg) under /etc/vexor/logs/filters/.

* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-11
- Log-data-driven checks: log alert rules gain mode=match|absence (dead-man),
  warn/crit thresholds, level filter and per-host grouping; each becomes a
  first-class passive Naemon service (flows into SLA/BSM/notifications).
  New /api/v1/log-checks catalog + /for-host bulk apply, and an alert-history
  log (/api/v1/log-alerts/history) backed by migrations 003 + 005.
- Configurable retention: global time + disk cap (maxDiskUsage bytes/percent)
  via logs.env, plus per-host overrides (migration 004) trimmed daily by the
  new vexor-logs-retention-enforcer.timer (enabled on install).
- Native syslog receiver (RFC3164/5424, auto-parsed) toggled from Settings.
- Ship migrations 003/004/005, retention-enforcer wrapper/.service/.timer;
  ensure logs.env is 0660 root:vexor so the API can persist settings.
- Fix naemon_passive lock path (/var/lock -> /run/vexor) so passive results
  submit under the vexor user.

* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-10
- evaluator: only submit a passive PROCESS_SERVICE_CHECK_RESULT when the rule's
  host_binding is a valid, known Naemon host. A stale/garbage binding (e.g. a
  deleted host, or a rule inserted out-of-band) previously made naemon reject
  the external command every poll cycle, spamming naemon.log. Mirrors the
  write-time validation already done in naemon_passive (defence in depth).
* Sat Jun 06 2026 Vexor <release@sayonara.dyndns.org> - 0.1.0-9
- install-linux-agent.sh (vector): detect built-in vexor-vector and skip (logs already shipped); otherwise ship a dedicated vexor-log-agent.service running vector as root with our /etc/vector/vector.toml instead of blindly enabling a non-existent vector.service (fixes 'Unit vector.service does not exist' on Vexor nodes)
* Fri May 22 2026 Copilot <copilot@vexor> - 0.1.0-7
- Ship polkit rule (91-vexor-victorialogs.rules) so vexor user can restart
  victorialogs without sudo (sudo blocked by NoNewPrivileges hardening).
- Ship vexor-cpanm-install wrapper + tightened sudoers (NOPASSWD only on
  the wrapper which validates Perl module names; replaces wildcards).
- Ship schema-drift migration (hosts.last_state, report_schedules.name/
  params/enabled/last_status) + postinstall script that applies migrations
  and reloads polkit on every upgrade.
- naemon_passive: verify host_binding refers to a known Naemon host before
  writing the service stanza; surface reload failures back to API caller
  (HTTP 409) and roll back broken stanzas so naemon never gets stuck.

* Fri May 22 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.1.0-5
- Bundle install-linux-agent-interactive.sh and install-windows-agent-interactive.ps1
  (previously hand-copied; now RPM-owned and served by /api/v1/logs/install-scripts).
- Backend: validate host_binding (prevents Naemon object injection), AnyHttpUrl
  validation on vexor_logs_url, atomic logs.env writes, surface vexor-victorialogs
  restart failure as HTTP 500, clean up decrypted SSH keys in shipper /tmp.
* Tue Nov 18 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.1.0-4
- Ship settings/storage/saved-searches/histogram/export/test-query/
  filter-library/shipper-deploy routers and the naemon passive helper.
- Bundle 12 starter filter JSONs under /etc/vexor/logs/filters/.
- Bundle install-linux-agent.sh and install-windows-agent.ps1.
- Persist retention via VEXOR_LOGS_RETENTION_DAYS in /etc/vexor/logs.env.
* Mon Nov 17 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.1.0-1
- Initial release: API plugin + log alerts evaluator.
