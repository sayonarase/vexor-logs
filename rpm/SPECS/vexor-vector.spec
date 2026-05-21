%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-vector
Version:        0.55.0
Release:        2%{?dist}
Summary:        Vector agent packaged with Vexor Logs defaults
License:        MPL-2.0
URL:            https://vector.dev/
Source0:        vector-%{version}-x86_64-unknown-linux-musl.tar.gz
Source1:        vector-default.toml
Source2:        vexor-vector.service
Source3:        logs.env.example
BuildArch:      x86_64
Requires:       systemd
Conflicts:      vector

%description
Vector (by Datadog, MPL-2.0) packaged for Vexor with a default configuration
that ships /var/log, journald and syslog to a VictoriaLogs endpoint defined
in /etc/vexor/logs.env. Use as an alternative to vexor-fluentbit.

%prep
%setup -q -c -T
tar xzf %{SOURCE0}

%install
install -d %{buildroot}/usr/bin
install -d %{buildroot}/etc/vexor/logs
install -d %{buildroot}/usr/lib/systemd/system
# upstream tarball ships ./vector-x86_64-unknown-linux-musl/bin/vector
VBIN=$(find . -type f -name vector -perm -u+x | head -1)
install -m 0755 "$VBIN" %{buildroot}/usr/bin/vector
install -m 0644 %{SOURCE1} %{buildroot}/etc/vexor/logs/vector.toml
install -m 0644 %{SOURCE2} %{buildroot}/usr/lib/systemd/system/vexor-vector.service
install -m 0644 %{SOURCE3} %{buildroot}/etc/vexor/logs.env.example

%post
%systemd_post vexor-vector.service
if [ ! -f /etc/vexor/logs.env ]; then
    cp /etc/vexor/logs.env.example /etc/vexor/logs.env
fi

%preun
%systemd_preun vexor-vector.service

%postun
%systemd_postun_with_restart vexor-vector.service

%files
/usr/bin/vector
%config(noreplace) /etc/vexor/logs/vector.toml
/etc/vexor/logs.env.example
/usr/lib/systemd/system/vexor-vector.service

%changelog
* Mon Nov 17 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.55.0-1
- Initial Vector wrapper for Vexor Logs.
