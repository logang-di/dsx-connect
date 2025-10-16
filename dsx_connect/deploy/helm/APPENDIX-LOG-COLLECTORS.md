# DSX Log Collectors and Fan‑Out (Appendix)

This appendix describes how DSX emits scan‑result events and how to deploy log collectors to receive and forward those events to your SIEM(s).

- Emission: DSX emits compact JSON over syslog (TCP/TLS recommended). Each payload includes top‑level fields:
  - `event: "scan_result"`, `job_id`, `status` (scanned/action succeeded/action failed), `id`
  - Nested objects: `scan_request`, `verdict`, `item_action`
- Transport (worker env):
  - `DSXCONNECT_SYSLOG__TRANSPORT=tcp|tls|udp` (tcp recommended)
  - `DSXCONNECT_SYSLOG__SYSLOG_SERVER_URL=<collector svc>`
  - `DSXCONNECT_SYSLOG__SYSLOG_SERVER_PORT=514`

## Option A — rsyslog (default collector)

A small Helm subchart (`charts/rsyslog`) deploys upstream `rsyslog/rsyslog` with a TCP 514 listener and JSON pass‑through.

Install:
```bash
helm upgrade --install rsyslog \
  dsx_connect/deploy/helm/charts/rsyslog
```

Values (excerpt):
```yaml
service:
  type: ClusterIP
  tcpPort: 514
  enableUDP: true
config:
  writeToStdout: true
  writeToFile: false
  filePath: /var/log/dsx.log
  hec:
    enabled: false
    host: splunk.example.com
    port: 8088
    usehttps: true
    restpath: /services/collector
    token: YOUR_TOKEN
  forward:
    enabled: true
    target: siem.example.com
    port: 6514
    tls: true
    permittedPeer: siem.example.com
```

DSX → rsyslog (worker .env):
```bash
DSXCONNECT_SYSLOG__TRANSPORT=tcp
DSXCONNECT_SYSLOG__SYSLOG_SERVER_URL=rsyslog
DSXCONNECT_SYSLOG__SYSLOG_SERVER_PORT=514
```

Local test via port‑forward:
```bash
kubectl port-forward svc/rsyslog 1514:514
# then point DSX to 127.0.0.1:1514
```

## Option B — syslog‑ng (OSE) reference

Minimal `syslog-ng.conf`:
```conf
@version: 3.38
options { chain-hostnames(no); flush-lines(1); keep-hostname(yes); };
source s_net {
  syslog(ip("0.0.0.0") transport("tcp") port(514));
  syslog(ip("0.0.0.0") transport("udp") port(514));
};
destination d_stdout { file("/dev/stdout"); };
log { source(s_net); destination(d_stdout); };
```

Fan‑out to Splunk HEC (example):
```conf
destination d_splunk {
  http(url("https://splunk:8088/services/collector")
       headers("Authorization: Splunk $TOKEN")
       method("POST") body-template("$MSG\n"));
};
log { source(s_net); destination(d_splunk); };
```

## Option C — Fluent Bit reference

Fluent Bit can replace rsyslog/syslog‑ng and provides first‑class outputs:
```ini
[INPUT]
    Name   syslog
    Mode   tcp
    Listen 0.0.0.0
    Port   514
    Parser json

[OUTPUT]
    Name   splunk
    Match  *
    Host   splunk.example.com
    Port   8088
    TLS    On
    Splunk_Token YOUR_TOKEN
    Splunk_Send_Raw On

[OUTPUT]
    Name   cloudwatch_logs
    Match  *
    region us-west-2
    log_group_name  dsx-connect
    log_stream_name dsx-${HOSTNAME}
```

## Notes
- Prefer TCP/TLS; kubectl port‑forward is TCP only.
- Collectors can write to `/dev/stdout` (seen via `kubectl logs`) and/or to persistent volumes.
- For production fan‑out at scale, Fluent Bit/Vector generally offer the most destination plugins and buffering options; rsyslog/syslog‑ng remain excellent for high‑performance syslog and simple HTTP forwarding.
