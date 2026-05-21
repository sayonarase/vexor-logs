%global vl_version 1.50.0
%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-victorialogs
Version:        %{vl_version}
Release:        1%{?dist}
Summary:        VictoriaLogs daemon packaged for Vexor
License:        ASL 2.0
URL:            https://github.com/VictoriaMetrics/VictoriaLogs
Source0:        victoria-logs-linux-amd64-v%{vl_version}.tar.gz
Source1:        victorialogs.yaml
Source2:        vexor-victorialogs.service
BuildArch:      x86_64
Requires:       systemd

%description
VictoriaLogs is a fast, cost-effective open source log database. This package
ships the upstream binary, a default configuration file, and a systemd unit
configured for use as the storage backend for Vexor Logs. The HTTP API is
bound to 127.0.0.1:9428 by default and is accessed via the Vexor API proxy.

%prep
%setup -q -c -T
tar xzf %{SOURCE0}

%install
install -d %{buildroot}/usr/bin
install -d %{buildroot}/etc/vexor/logs
install -d %{buildroot}/var/lib/vexor/victorialogs
install -d %{buildroot}/usr/lib/systemd/system
install -m 0755 victoria-logs-prod %{buildroot}/usr/bin/victoria-logs
install -m 0644 %{SOURCE1} %{buildroot}/etc/vexor/logs/victorialogs.yaml
install -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/vexor-victorialogs.service

%pre
getent group vexor >/dev/null || groupadd -r vexor
getent passwd vexor >/dev/null || useradd -r -g vexor -d /opt/vexor -s /sbin/nologin vexor

%post
%systemd_post vexor-victorialogs.service
chown -R vexor:vexor /var/lib/vexor/victorialogs

%preun
%systemd_preun vexor-victorialogs.service

%postun
%systemd_postun_with_restart vexor-victorialogs.service

%files
/usr/bin/victoria-logs
%config(noreplace) /etc/vexor/logs/victorialogs.yaml
/usr/lib/systemd/system/vexor-victorialogs.service
%dir %attr(0750,vexor,vexor) /var/lib/vexor/victorialogs
%dir /etc/vexor/logs

%changelog
* Mon Nov 17 2026 sayonarase <sayonarase@users.noreply.github.com> - 1.50.0-1
- Initial package; ships upstream VictoriaLogs 1.50.0 for Vexor.
