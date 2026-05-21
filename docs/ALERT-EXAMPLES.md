# Alert rule examples

All examples are LogsQL queries. Drop them into the *Log Alerts* page.

| Name                    | Query                                                | Window | Threshold | Severity  |
|-------------------------|------------------------------------------------------|--------|-----------|-----------|
| auth-bruteforce         | `_msg:"authentication failure"`                      | 300 s  | 10        | warning   |
| sshd-many-fails         | `service:sshd AND _msg:"Failed password"`            | 600 s  | 20        | warning   |
| kernel-segfault         | `_msg:segfault`                                      | 60 s   | 1         | critical  |
| oom-killer              | `_msg:"Out of memory"`                               | 60 s   | 1         | critical  |
| disk-errors             | `_msg:"I/O error" OR _msg:"end_request: I/O error"`  | 60 s   | 1         | critical  |
| selinux-denials         | `_msg:"avc: *denied"`                                | 600 s  | 5         | warning   |
| sudo-failure            | `_msg:"sudo: pam_unix"`                              | 300 s  | 3         | warning   |
| firewalld-blocked-flood | `service:firewalld AND _msg:reject`                  | 60 s   | 100       | warning   |
| systemd-unit-failures   | `_msg:"Failed to start"`                             | 300 s  | 1         | warning   |
| nginx-5xx               | `service:nginx AND _msg:" 5[0-9]{2} "`               | 60 s   | 25        | warning   |
