# NAT Performance Test Tool (v0.1)

A tool for testing how a NAT device/gateway behaves under a large number of concurrent
connections. Pure Node.js implementation — **no npm install required**. Just copy it onto any
machine with Node.js (>=14) and run it, which makes it easy to deploy on real client/server
machines sitting on either side of the NAT under test.

## Project Structure

```
nat-perf-test/
  server/
    server.js          Server: HTTP/TCP/UDP test listeners + live Dashboard
    public/dashboard.html
  client/
    client.js           Client: control panel + connection ramp-up logic
    public/panel.html
```

## Quick Start

### 1. Start the server (on the public/reachable side of the NAT under test)

```bash
cd server
node server.js
# or with custom ports:
# HTTP_PORT=8080 TCP_PORT=9090 UDP_PORT=9091 DASHBOARD_PORT=8090 node server.js
```

Open `http://<server-ip>:8090` for the live Dashboard:
- Total connection count / breakdown by HTTP · TCP · UDP
- Per-connection type, source IP, source port, and duration
- Auto-refreshes every second via SSE — no manual refresh needed

### 2. Start the client (inside the NAT)

```bash
cd client
node client.js
# or with a custom control panel port: CONTROL_PORT=8070 node client.js
```

Open `http://<client-ip>:8070` and configure:
- **Server address / per-protocol ports**
- **Target connection count per protocol** (HTTP / TCP / UDP set independently; 0 skips a protocol)
- **Warmup duration (sec)** and **number of ramp-up steps**: the total target is built up in
  linear batches over the warmup window, avoiding an instant flood and more closely mirroring
  how NAT table entries grow in real traffic
- Click **Start Test** — status moves from `warmup` → `steady` (target reached and held), with
  live progress bars and stats
- Click **Stop** to close all established connections

## How Each Protocol's "Connection" Is Defined

| Protocol | How it's counted |
|---|---|
| HTTP | Client opens a long-lived request to `/keepalive` (chunked response never ends); server counts by underlying TCP socket |
| TCP | Plain long-lived TCP connection; client sends a heartbeat byte every 30s to survive idle timeouts on middleboxes |
| UDP | Connectionless — client sends a keepalive packet every 5s to maintain the NAT mapping; server treats a source IP:port as "connected" as long as it's been active within a timeout window (default 15s, configurable via `UDP_TIMEOUT_MS`) |

## Binding and Remote Access

Both `client.js` and `server.js` listen on `0.0.0.0` by default (all interfaces), so they're
already reachable from other machines on the network — not just `localhost`. You can override
the bind address if needed:

```bash
# client control panel
CONTROL_HOST=0.0.0.0 CONTROL_PORT=8070 node client.js

# server (applies to the HTTP/TCP/UDP test ports and the dashboard)
BIND_HOST=0.0.0.0 node server.js
```

If you're putting an nginx reverse proxy in front of the control panel or dashboard, note that
the `/events` endpoint uses **Server-Sent Events (SSE)**, which nginx buffers by default — this
will make the live updates appear stuck or delayed unless you disable buffering. Example config:

```nginx
server {
    listen 80;
    server_name your-domain-or-ip;

    location / {
        proxy_pass http://127.0.0.1:8070;  # client control panel (use 8090 for the server dashboard)
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /events {
        proxy_pass http://127.0.0.1:8070/events;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;      # critical for SSE
        proxy_cache off;
        chunked_transfer_encoding off;
        proxy_read_timeout 3600s; # keep the stream open
    }
}
```

## Latency Measurement

Two kinds of latency are tracked separately, and both are bucketed by whichever phase
(warmup/steady) was active when the sample was taken — shown as two tables on the client panel:

| Metric | What it measures | When it's sampled |
|---|---|---|
| **Connect latency** (warmup table) | Time to establish the connection: TCP handshake time, HTTP time-to-first-byte, or UDP's first echo round-trip (UDP has no handshake, so the first reply stands in for one) | Once per connection, at establishment |
| **RTT latency** (steady table) | Ongoing round-trip time from periodic ping/pong heartbeats | Every 2 seconds, for the life of the connection |

Each table shows count / avg / min / max per protocol. Only rolling aggregates are kept (no
per-sample history), so memory use stays flat during long-running tests.

To make RTT measurable, the server now echoes back whatever it receives:
- **TCP**: raw bytes echoed back as-is.
- **UDP**: the received datagram is echoed back to the sender.
- **HTTP**: the client sends the `/keepalive` request as a chunked `POST` instead of `GET`, so it
  can stream `PING:<timestamp>` lines in the request body while still reading the open response
  stream — the server replies with a `PONG:<timestamp>` line on that same response stream. This
  gives a duplex ping/pong channel without opening a second socket.

## Known Limitations (v0.1)

- No automated report export (CSV/charts) yet — real-time visualization only
- No separate success-rate/connection-time breakdown for warmup vs. steady phase yet
- At very large connection counts (e.g. 100k), watch OS limits (`ulimit -n`, ephemeral port
  range) on the client machine and raise them as needed
- For carrier-grade/multi-layer NAT testing, make sure the server has a reachable address and
  the client's traffic actually traverses the NAT under test

## FAQ

**Q: Connection count won't climb / lots of failures?**
Check the client machine's `ulimit -n` (open file descriptor limit) and available ephemeral
ports; run `ulimit -n 65535` before starting the client if needed.

**Q: The UDP count is lower than the number of packets actually sent?**
UDP is connectionless — the server simulates connection state via "has this source IP:port been
active within the timeout window." If the client's keepalive interval (default 5s) is longer
than the server's timeout (default 15s), some sessions may be misjudged as expired; tune the two
to match.
