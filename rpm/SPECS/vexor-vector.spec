%define _build_id_links none
%global __os_install_post %{nil}
%global debug_package %{nil}
AutoReq: no
AutoProv: no

Name:           vexor-vector
Version:        0.58.0
Release:        1%{?dist}
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

# --- Vector 0.57 migration -------------------------------------------------
# 0.57 made ${VAR} interpolation opt-in and added sink template confinement.
# Existing installs keep their own logs.env and their own %config(noreplace)
# vector.toml, so without this migration Vector 0.57 refuses to start with
# "invalid uri character" and/or a confinement error. Both steps are idempotent.
if ! grep -q '^VECTOR_DANGEROUSLY_ALLOW_ENV_VAR_INTERPOLATION=' /etc/vexor/logs.env 2>/dev/null; then
    printf '\n# Added on upgrade to Vector 0.57: interpolation is opt-in from 0.57 on,\n# and vector.toml resolves its endpoint from VEXOR_LOGS_URL.\nVECTOR_DANGEROUSLY_ALLOW_ENV_VAR_INTERPOLATION=true\n' >> /etc/vexor/logs.env
fi

CFG=/etc/vexor/logs/vector.toml
# --- Vector 0.58 migration -------------------------------------------------
# 0.57 wrongly flagged our {{ host }} / {{ service }} labels as unconfined
# templates, so the 0.57 packages switched confinement off in vector.toml.
# 0.58 fixes that false positive, so the bypass is no longer needed and is
# removed here. vector.toml is %config(noreplace), so an existing install keeps
# its own file and would otherwise carry the bypass forever. The edit is
# verified before it is kept: if the resulting config does not validate, the
# original is put back and the bypass stays.
if [ -f "$CFG" ] && grep -q '^dangerously_allow_unconfined_template_resolution' "$CFG"; then
    cp -a "$CFG" "$CFG.rpmsave-0.57"
    awk '
      /^dangerously_allow_unconfined_template_resolution/ { next }
      /^#/ && (/confinement/ || /25898/ || /26011/ || /dangerously_allow_unconfined/) { next }
      { print }
    ' "$CFG.rpmsave-0.57" > "$CFG.new-0.58"
    (
        set -a
        [ -f /etc/vexor/logs.env ] && . /etc/vexor/logs.env
        set +a
        /usr/bin/vector validate --no-environment "$CFG.new-0.58"
    ) >/dev/null 2>&1
    if [ $? -eq 0 ]; then
        cat "$CFG.new-0.58" > "$CFG"
        rm -f "$CFG.new-0.58"
        echo "vexor-vector: removed the 0.57 template-confinement bypass from $CFG" >&2
        echo "vexor-vector: previous file kept as $CFG.rpmsave-0.57" >&2
    else
        rm -f "$CFG.new-0.58"
        echo "vexor-vector: kept $CFG unchanged - it does not validate without" >&2
        echo "vexor-vector: dangerously_allow_unconfined_template_resolution." >&2
    fi
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
* Thu Sep 03 2026 Vexor <ops@vexormon.com> - 0.58.0-1
- Update bundled Vector to 0.58.0. Among the fixes are several that affect log
  delivery on a busy server: metric sinks using a disk buffer could stall
  permanently after 10-15 minutes, a disk buffer could look full after a crash
  and block new events, and a record too large to write tore down the whole
  pipeline instead of being dropped on its own.
- Remove the template-confinement bypass. Vector 0.57 wrongly treated our
  {{ host }} and {{ service }} labels as unconfined templates, so we had to set
  dangerously_allow_unconfined_template_resolution. Upstream fixed that false
  positive, so confinement is now on again. Existing configurations are
  migrated in %%post, and only if the result still validates.

* Wed Sep 02 2026 Vexor <ops@vexormon.com> - 0.57.0-2
- Create the Vector data_dir via StateDirectory=vector. vector.toml sets
  data_dir=/var/lib/vector, but no package owned that directory and the unit
  never declared it, so a fresh install (or any host with an emptied
  /var/lib) failed to start with 'data_dir does not exist'.

* Tue Aug 18 2026 Vexor <release@sayonara.dyndns.org> - 0.57.0-1
- Update bundled Vector to 0.57.0 (security-focused release with breaking changes).
- Vector 0.57 makes ${VAR} interpolation opt-in and adds sink template confinement.
  Our endpoint is "${VEXOR_LOGS_URL:-...}/insert" and our labels are whole-value
  event references, so BOTH are affected: without migration 0.57 fails to load the
  config with "invalid uri character" and then with a confinement error.
- logs.env(.example) now sets VECTOR_DANGEROUSLY_ALLOW_ENV_VAR_INTERPOLATION=true and
  the default vector.toml sets dangerously_allow_unconfined_template_resolution.
  The endpoint is deliberately NOT hard-coded: on a host shipping to a remote Vexor
  server it must resolve to that server, not to localhost.
- %post migrates EXISTING installs, since logs.env is only seeded when absent and
  vector.toml is %config(noreplace) - a plain version bump would have left every
  existing install unable to start. Both steps are idempotent and the vector.toml
  edit is skipped (with a warning) unless the loki sink is the file's last table.
- Agent configs written by install-linux-agent.sh are unaffected: they are rendered
  by the shell at install time and use a literal http sink URI with no templates.
  Verified against 0.57.
* Sat Jun 20 2026 Vexor <release@sayonara.dyndns.org> - 0.56.0-2
- Fix host label: the built-in shipper tagged every log host="unknown" because
  get_env_var("HOSTNAME") is empty under systemd. Use get_hostname() so logs are
  tagged with the real node hostname. %config(noreplace) keeps existing configs;
  restart vexor-vector to pick up the new default on a fresh install.

* Thu Jun 04 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.56.0-1
- Update bundled Vector to 0.56.0.
* Thu May 21 2026 sayonarase <sayonarase@users.noreply.github.com> - 0.55.0-1
- Initial Vector wrapper for Vexor Logs.
