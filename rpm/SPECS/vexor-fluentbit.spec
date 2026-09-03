%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-fluentbit
Epoch:          1
Version:        1.0.0
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
A configuration overlay that pre-configures fluent-bit to ship /var/log
and journald records to the VictoriaLogs endpoint defined in
/etc/vexor/logs.env. Fluent Bit is the alternative log shipper; vexor-vector
is the default one.

The fluent-bit binary itself is not shipped here. It is pulled from EPEL,
which Vexor already requires, and is kept up to date by dnf along with the
rest of the distribution. This package is versioned independently for that
reason: its version describes the configuration, not Fluent Bit.

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
* Thu Sep 03 2026 sayonarase <sayonarase@users.noreply.github.com> - 1:1.0.0-1
- Point the service at /usr/bin/fluent-bit, where EPEL installs it. The unit
  referenced the path used by Fluent Bit's own RPM repository, which is not
  configured on a Vexor server, so the service could never start.
- Send to VictoriaLogs' /insert/loki/api/v1/push. Fluent Bit's default Loki path
  is rejected by VictoriaLogs, so no log line was ever accepted.
- Normalise the message field so log lines are readable. The tail input names it
  "log" and the systemd input names it "MESSAGE"; VictoriaLogs reads "_msg", so
  every line displayed as a placeholder telling us the field was missing.
- Use an absolute path to the parser definitions, which resolved relative to the
  config directory and was never found.
- Set HOSTNAME in the unit; systemd does not provide it, so the host label the
  configuration adds was empty.
- Version the overlay independently of Fluent Bit. It never shipped a Fluent Bit
  binary, so its old version number described nothing that was installed.

* Thu May 21 2026 sayonarase <sayonarase@users.noreply.github.com> - 5.0.5-1
- Initial Fluent Bit wrapper for Vexor Logs.
