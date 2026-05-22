%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-logs
Version:        0.1.0
Release:        7%{?dist}
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

# Apply DB schema drift migrations - idempotent (v0.1.0-7)
if [ -x /usr/share/vexor-logs/vexor-logs-postinstall.sh ]; then
    /usr/share/vexor-logs/vexor-logs-postinstall.sh || :
fi

systemctl try-restart vexor-api.service 2>/dev/null || :

%preun
%systemd_preun vexor-log-alerts-evaluator.service

%postun
%systemd_postun_with_restart vexor-log-alerts-evaluator.service

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
/usr/share/vexor-logs/vexor-logs-postinstall.sh

%changelog
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
