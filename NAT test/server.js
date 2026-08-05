#!/usr/bin/env node
'use strict';

/**
 * NAT Performance Test - Server (v0.1)
 *
 * Listens simultaneously on:
 *   - HTTP long-lived connection test port (default 8080) -> GET /keepalive keeps a chunked response open
 *   - Raw TCP test port                   (default 9090) -> just holds the connection open
 *   - UDP test port                       (default 9091) -> connectionless; a "session" is simulated via last-seen time
 *   - Dashboard port                      (default 8090) -> serves a web UI + SSE stream of live stats
 *
 * Usage:
 *   node server.js
 *   HTTP_PORT=8080 TCP_PORT=9090 UDP_PORT=9091 DASHBOARD_PORT=8090 node server.js
 */

const http = require('http');
const net = require('net');
const dgram = require('dgram');
const fs = require('fs');
const path = require('path');
const url = require('url');

const CONFIG = {
  httpPort: parseInt(process.env.HTTP_PORT || '8080', 10),
  tcpPort: parseInt(process.env.TCP_PORT || '9090', 10),
  udpPort: parseInt(process.env.UDP_PORT || '9091', 10),
  dashboardPort: parseInt(process.env.DASHBOARD_PORT || '8090', 10),
  // Bind host for all listeners below. Defaults to 0.0.0.0 so the server is
  // reachable from other machines (e.g. clients on the other side of the NAT
  // under test, or behind an nginx reverse proxy for the dashboard).
  bindHost: process.env.BIND_HOST || '0.0.0.0',
  // If no UDP packet arrives within this window, the "connection" (NAT mapping) is considered expired
  udpSessionTimeoutMs: parseInt(process.env.UDP_TIMEOUT_MS || '15000', 10),
};

// ---------------- Connection registry ----------------
// id -> { type, ip, port, connectedAt, lastActivity }
const connections = new Map();
let seq = 0;

function registerConnection(type, ip, port) {
  const id = `${type}-${++seq}`;
  connections.set(id, {
    id, type, ip, port,
    connectedAt: Date.now(),
    lastActivity: Date.now(),
  });
  return id;
}

function touch(id) {
  const c = connections.get(id);
  if (c) c.lastActivity = Date.now();
}

function removeConnection(id) {
  connections.delete(id);
}

function getStats() {
  const byType = { http: 0, tcp: 0, udp: 0 };
  const byIp = new Map(); // ip -> { ip, http, tcp, udp }
  const list = [];
  for (const c of connections.values()) {
    byType[c.type] = (byType[c.type] || 0) + 1;

    if (!byIp.has(c.ip)) byIp.set(c.ip, { ip: c.ip, http: 0, tcp: 0, udp: 0 });
    byIp.get(c.ip)[c.type]++;

    list.push({
      id: c.id,
      type: c.type,
      ip: c.ip,
      port: c.port,
      durationSec: Math.round((Date.now() - c.connectedAt) / 1000),
    });
  }
  list.sort((a, b) => b.durationSec - a.durationSec);

  const ipList = Array.from(byIp.values()).map((e) => ({ ...e, total: e.http + e.tcp + e.udp }));
  ipList.sort((a, b) => b.total - a.total);

  return {
    total: connections.size,
    byType,
    distinctIps: ipList.length,
    byIp: ipList.slice(0, 1000), // cap so the UI stays responsive at scale
    connections: list.slice(0, 500), // cap the list so the UI stays responsive at scale
    timestamp: Date.now(),
  };
}

// ---------------- HTTP test port ----------------
// The client requests /keepalive and the server holds the chunked response open,
// occupying one NAT mapping per connection.
const httpServer = http.createServer((req, res) => {
  if (req.url === '/keepalive') {
    res.writeHead(200, {
      'Content-Type': 'text/plain; charset=utf-8',
      'Transfer-Encoding': 'chunked',
      Connection: 'keep-alive',
    });
    res.write('ok\n');
    const idleIv = setInterval(() => {
      try { res.write('.'); } catch (e) { clearInterval(idleIv); }
    }, 10000);

    // Duplex ping/pong: the client streams "PING:<ts>\n" lines in the request
    // body (chunked), and we echo each one back as "PONG:<ts>\n" on the
    // response stream, all over this same long-lived connection, so the
    // client can measure round-trip latency without opening a new socket.
    let buf = '';
    req.on('data', (chunk) => {
      buf += chunk.toString('utf8');
      let idx;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (line.startsWith('PING:')) {
          try { res.write('PONG:' + line.slice(5) + '\n'); } catch (e) {}
        }
      }
    });

    req.on('close', () => clearInterval(idleIv));
    return;
  }
  res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('nat-perf-test http server\n');
});

// Track each underlying TCP socket as one connection (with HTTP keep-alive,
// a single socket may serve multiple requests over time).
httpServer.on('connection', (socket) => {
  const ip = socket.remoteAddress || 'unknown';
  const port = socket.remotePort || 0;
  const id = registerConnection('http', ip, port);
  socket.on('close', () => removeConnection(id));
});

httpServer.listen(CONFIG.httpPort, CONFIG.bindHost, () => {
  console.log(`[http] listening on ${CONFIG.bindHost}:${CONFIG.httpPort}`);
});

// ---------------- Raw TCP test port ----------------
const tcpServer = net.createServer((socket) => {
  const ip = socket.remoteAddress || 'unknown';
  const port = socket.remotePort || 0;
  const id = registerConnection('tcp', ip, port);
  socket.on('data', (data) => {
    touch(id);
    // Echo raw bytes back so the client can measure round-trip latency
    try { socket.write(data); } catch (e) {}
  });
  socket.on('close', () => removeConnection(id));
  socket.on('error', () => {}); // ignore resets etc. so the process never crashes
});

tcpServer.listen(CONFIG.tcpPort, CONFIG.bindHost, () => {
  console.log(`[tcp] listening on ${CONFIG.bindHost}:${CONFIG.tcpPort}`);
});

// ---------------- UDP test port ----------------
// UDP is connectionless, so we simulate a "connection"/NAT mapping by tracking
// how recently we've seen a packet from a given remote ip:port. If nothing
// arrives within udpSessionTimeoutMs, the session is considered expired.
const udpSocket = dgram.createSocket('udp4');
const udpSessions = new Map(); // "ip:port" -> connectionId

udpSocket.on('message', (msg, rinfo) => {
  const key = `${rinfo.address}:${rinfo.port}`;
  let id = udpSessions.get(key);
  if (!id) {
    id = registerConnection('udp', rinfo.address, rinfo.port);
    udpSessions.set(key, id);
  } else {
    touch(id);
  }
  // Echo the packet back so the client can measure round-trip latency
  udpSocket.send(msg, rinfo.port, rinfo.address);
});

udpSocket.on('error', (err) => {
  console.error('[udp] error', err);
});

udpSocket.bind(CONFIG.udpPort, CONFIG.bindHost, () => {
  console.log(`[udp] listening on ${CONFIG.bindHost}:${CONFIG.udpPort}`);
});

setInterval(() => {
  const now = Date.now();
  for (const [key, id] of udpSessions.entries()) {
    const c = connections.get(id);
    if (!c || now - c.lastActivity > CONFIG.udpSessionTimeoutMs) {
      udpSessions.delete(key);
      removeConnection(id);
    }
  }
}, 2000);

// ---------------- Dashboard (UI + SSE) ----------------
const publicDir = path.join(__dirname, 'public');

const dashboardServer = http.createServer((req, res) => {
  const parsed = url.parse(req.url);

  if (parsed.pathname === '/api/stats') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(getStats()));
    return;
  }

  if (parsed.pathname === '/events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    const iv = setInterval(() => {
      res.write(`data: ${JSON.stringify(getStats())}\n\n`);
    }, 1000);
    req.on('close', () => clearInterval(iv));
    return;
  }

  let filePath = parsed.pathname === '/' ? '/dashboard.html' : parsed.pathname;
  filePath = path.join(publicDir, filePath);
  if (!filePath.startsWith(publicDir)) {
    res.writeHead(403); res.end('forbidden'); return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      console.error(`[dashboard] 404: tried to read "${filePath}" -> ${err.code}`);
      res.writeHead(404); res.end('not found'); return;
    }
    const ext = path.extname(filePath);
    const type = ext === '.html' ? 'text/html' : ext === '.js' ? 'application/javascript' : ext === '.css' ? 'text/css' : 'text/plain';
    res.writeHead(200, { 'Content-Type': type });
    res.end(data);
  });
});

dashboardServer.listen(CONFIG.dashboardPort, CONFIG.bindHost, () => {
  console.log(`[dashboard] listening on ${CONFIG.bindHost}:${CONFIG.dashboardPort}`);
});

console.log('NAT perf-test server started with config:', CONFIG);
