%global vl_version 1.51.0
%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-victorialogs
Version:        %{vl_version}
Release:        2%{?dist}
Summary:        VictoriaLogs daemon packaged for Vexor
License:        ASL 2.0
URL:            https://github.com/VictoriaMetrics/VictoriaLogs
Source0:        victoria-logs-linux-amd64-v%{vl_version}.tar.gz
Source1:        victorialogs.yaml
Source2:        vexor-victorialogs.service
Source3:        vexor-victorialogs
Source4:        vexor-victorialogs-syslog.conf
BuildArch:      x86_64
Requires:       systemd

%description
VictoriaLogs is a fast, cost-effective open source log database. This package
ships the upstream binary, a default configuration file, the
``vexor-victorialogs`` launcher that wires /etc/vexor/logs.env knobs (such as
``VEXOR_LOGS_RETENTION_DAYS``) into the VictoriaLogs CLI flags, and a systemd
unit. The HTTP API is bound to 127.0.0.1:9428 by default and is accessed via
the Vexor API proxy.

%prep
%setup -q -c -T
tar xzf %{SOURCE0}

%install
install -d %{buildroot}/usr/bin
install -d %{buildroot}/etc/vexor/logs
install -d %{buildroot}/var/lib/vexor/victorialogs
install -d %{buildroot}/usr/lib/systemd/system
install -m 0755 victoria-logs-prod %{buildroot}/usr/bin/victoria-logs
install -m 0755 %{SOURCE3} %{buildroot}/usr/bin/vexor-victorialogs
install -m 0644 %{SOURCE1} %{buildroot}/etc/vexor/logs/victorialogs.yaml
install -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/vexor-victorialogs.service
install -d %{buildroot}/usr/lib/systemd/system/vexor-victorialogs.service.d
install -m 0644 %{SOURCE4} %{buildroot}/usr/lib/systemd/system/vexor-victorialogs.service.d/vexor-syslog.conf

%pre
getent group vexor >/dev/null || groupadd -r vexor
getent passwd vexor >/dev/null || useradd -r -g vexor -d /opt/vexor -s /sbin/nologin vexor

%post
%systemd_post vexor-victorialogs.service
chown -R vexor:vexor /var/lib/vexor/victorialogs
# Ensure retention default is present in env file (vexor-logs ships the
# canonical env file but we may be installed standalone).
if [ -f /etc/vexor/logs.env ] && ! grep -q '^VEXOR_LOGS_RETENTION_DAYS=' /etc/vexor/logs.env; then
    echo 'VEXOR_LOGS_RETENTION_DAYS=90' >> /etc/vexor/logs.env
fi
systemctl daemon-reload || :
systemctl try-restart vexor-victorialogs.service 2>/dev/null || :

%preun
%systemd_preun vexor-victorialogs.service

%postun
%systemd_postun_with_restart vexor-victorialogs.service

%files
/usr/bin/victoria-logs
/usr/bin/vexor-victorialogs
%config(noreplace) /etc/vexor/logs/victorialogs.yaml
/usr/lib/systemd/system/vexor-victorialogs.service
%dir /usr/lib/systemd/system/vexor-victorialogs.service.d
/usr/lib/systemd/system/vexor-victorialogs.service.d/vexor-syslog.conf
%dir %attr(0750,vexor,vexor) /var/lib/vexor/victorialogs
%dir /etc/vexor/logs

%changelog
* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 1.51.0-2
- Launcher: wire optional disk-based retention (VEXOR_LOGS_DISK_MODE/BYTES/
  PERCENT) and a native syslog receiver (VEXOR_LOGS_SYSLOG_UDP/TCP) into the
  VictoriaLogs flags.
- Ship a service drop-in granting CAP_NET_BIND_SERVICE so the vexor user can
  bind privileged syslog ports (e.g. 514) under NoNewPrivileges hardening.

* Sat Jun 20 2026 sayonarase <sayonarase@users.noreply.github.com> - 1.51.0-1
- Update bundled VictoriaLogs to upstream v1.51.0.

* Tue Nov 18 2025 sayonarase <sayonarase@users.noreply.github.com> - 1.50.0-2
- Add /usr/bin/vexor-victorialogs launcher that reads /etc/vexor/logs.env
  (VEXOR_LOGS_RETENTION_DAYS, VEXOR_LOGS_LISTEN, VEXOR_LOGS_STORAGE).
- Service unit now sources /etc/vexor/logs.env and execs the launcher.
* Mon Nov 17 2025 sayonarase <sayonarase@users.noreply.github.com> - 1.50.0-1
- Initial package; ships upstream VictoriaLogs 1.50.0 for Vexor.
