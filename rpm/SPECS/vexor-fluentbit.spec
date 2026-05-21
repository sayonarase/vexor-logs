%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-fluentbit
Version:        5.0.5
Release:        1%{?dist}
Summary:        Fluent Bit packaged with Vexor Logs defaults
License:        Apache-2.0
URL:            https://fluentbit.io/
Source1:        fluentbit-default.conf
Source2:        vexor-fluentbit.service
Source3:        logs.env.example
BuildArch:      x86_64
# Use upstream RPM for the binary itself; we only layer a default config.
Requires:       fluent-bit
Requires:       systemd

%description
A configuration overlay that pre-configures upstream fluent-bit to ship
/var/log, journald and syslog records to a VictoriaLogs endpoint defined
in /etc/vexor/logs.env. The upstream fluent-bit binary is pulled via the
Fluent Bit RPM repository.

%prep
%setup -q -c -T

%install
install -d %{buildroot}/etc/vexor/logs
install -d %{buildroot}/usr/lib/systemd/system
install -m 0644 %{SOURCE1} %{buildroot}/etc/vexor/logs/fluentbit.conf
install -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/vexor-fluentbit.service
install -m 0644 %{SOURCE3} %{buildroot}/etc/vexor/logs.env.example

%post
%systemd_post vexor-fluentbit.service
if [ ! -f /etc/vexor/logs.env ]; then
    cp /etc/vexor/logs.env.example /etc/vexor/logs.env
fi

%preun
%systemd_preun vexor-fluentbit.service

%postun
%systemd_postun_with_restart vexor-fluentbit.service

%files
%config(noreplace) /etc/vexor/logs/fluentbit.conf
/etc/vexor/logs.env.example
/usr/lib/systemd/system/vexor-fluentbit.service

%changelog
* Mon Nov 17 2026 sayonarase <sayonarase@users.noreply.github.com> - 5.0.5-1
- Initial Fluent Bit wrapper for Vexor Logs.
