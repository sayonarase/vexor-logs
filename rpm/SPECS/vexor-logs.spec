%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-logs
Version:        0.1.0
Release:        3%{?dist}
Summary:        Vexor Logs server-side glue (API plugin + alert evaluator)
License:        Apache-2.0
URL:            https://github.com/sayonarase/vexor-logs
BuildArch:      noarch
Source0:        vexor-logs-api.tar.gz
Source1:        logs.env.example
Source2:        log-alerts-evaluator.service

Requires:       vexor-victorialogs
Requires:       vexor-api >= 0.1.0-6
Requires:       python3

%description
Vexor Logs glue package. Ships the FastAPI plugin (logs router, log-alerts
CRUD) into vexor-api's plugin directory, installs the alert-evaluator
systemd service, and provides the default /etc/vexor/logs.env file used by
both server and agents.

%prep
%setup -q -c -T
tar xzf %{SOURCE0}

%install
install -d %{buildroot}/opt/vexor/api/plugins/logs
cp -a vexor_logs_api/. %{buildroot}/opt/vexor/api/plugins/logs/
install -d %{buildroot}/etc/vexor
install -m 0644 %{SOURCE1} %{buildroot}/etc/vexor/logs.env.example
install -d %{buildroot}/usr/lib/systemd/system
install -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/vexor-log-alerts-evaluator.service
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
    cp /etc/vexor/logs.env.example /etc/vexor/logs.env
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
/etc/vexor/logs.env.example
/usr/lib/systemd/system/vexor-log-alerts-evaluator.service
/usr/bin/vexor-log-alerts-evaluator

%changelog
* Mon Nov 17 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.1.0-1
- Initial release: API plugin + log alerts evaluator.
