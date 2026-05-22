%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-logs
Version:        0.1.0
Release:        5%{?dist}
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

%changelog
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
