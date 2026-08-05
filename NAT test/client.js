#!/usr/bin/env node
'use strict';

/**
 * NAT Performance Test - Client (v0.1)
 *
 * Starts a local control panel (default http://localhost:8070) where you can
 * configure the target host, per-protocol target connection counts, and
 * warmup duration/steps. Clicking "Start" ramps up connections in steps
 * (warmup), then holds at the target (steady).
 *
 * Usage:
 *   node client.js
 *   CONTROL_PORT=8070 node client.js
 */

const http = require('http');
const net = require('net');
const dgram = require('dgram');
const fs = require('fs');
const path = require('path');
const url = require('url');

const CONTROL_PORT = parseInt(process.env.CONTROL_PORT || '8070', 10);
// Bind host for the control panel. Defaults to 0.0.0.0 so it's reachable from
// other machines on the subnet (e.g. from behind an nginx reverse proxy).
// Set CONTROL_HOST=127.0.0.1 if you want to restrict it to localhost only.
const CONTROL_HOST = process.env.CONTROL_HOST || '0.0.0.0';
const publicDir = path.join(__dirname, 'public');

// ---------------- Test state ----------------
let state = null;
let sockets = { http: [], tcp: [], udp: [] };
// Generation counter: incremented every time Start or Stop is called, so any
// in-flight ramp-up loop from a *previous* run immediately notices it's been
// superseded and stops opening new connections — even if Start was clicked
// again before the previous run finished. A simple boolean flag isn't enough
// here: if Start is clicked twice in quick succession, resetting a shared
// "stopping" flag back to false for the new run could let the old loop's
// in-flight sleep() resume and keep going, silently leaking sockets from the
// previous run on every restart until ports/file descriptors run out —
// which shows up as exactly the "sometimes it works, sometimes it doesn't"
// flakiness.
let currentGeneration = 0;

function closeAllSockets() {
  for (const type of ['http', 'tcp', 'udp']) {
    for (const h of sockets[type]) {
      try { (h.close ? h.close() : h.destroy()); } catch (e) {}
    }
    sockets[type] = [];
  }
}

function freshState() {
  return {
    phase: 'idle', // idle | warmup | steady | stopped
    startedAt: null,
    config: null,
    active: { http: 0, tcp: 0, udp: 0 },
    target: { http: 0, tcp: 0, udp: 0 },
    succeeded: { http: 0, tcp: 0, udp: 0 },
    failed: { http: 0, tcp: 0, udp: 0 },
  };
}
state = freshState();

// ---------------- Latency stats ----------------
// Two kinds of latency, tracked separately, and both bucketed by whichever
// phase (warmup/steady) was active at the moment of the sample:
//   - "connect": time to establish the connection (TCP handshake, HTTP TTFB,
//     or first UDP echo round-trip). Recorded once per connection.
//   - "rtt": ongoing round-trip time from periodic ping/pong heartbeats,
//     sampled every 2s for the life of the connection.
// Only rolling {count, sum, min, max} are kept — no per-sample history —
// so memory stays flat during long-running tests.
function newAgg() { return { count: 0, sum: 0, min: null, max: null }; }

function freshLatencyStats() {
  const mk = () => ({ http: newAgg(), tcp: newAgg(), udp: newAgg() });
  return {
    warmup: { connect: mk(), rtt: mk() },
    steady: { connect: mk(), rtt: mk() },
  };
}
let latencyStats = freshLatencyStats();

function pushAgg(agg, ms) {
  agg.count++;
  agg.sum += ms;
  agg.min = agg.min === null ? ms : Math.min(agg.min, ms);
  agg.max = agg.max === null ? ms : Math.max(agg.max, ms);
}

function summarizeAgg(agg) {
  return {
    count: agg.count,
    avgMs: agg.count ? Math.round((agg.sum / agg.count) * 10) / 10 : null,
    minMs: agg.min,
    maxMs: agg.max,
  };
}

function recordLatency(metric, protocol, ms) {
  const phase = state.phase === 'steady' ? 'steady' : 'warmup';
  pushAgg(latencyStats[phase][metric][protocol], ms);
}

function latencySnapshot() {
  const summarizePhase = (p) => ({
    connect: { http: summarizeAgg(p.connect.http), tcp: summarizeAgg(p.connect.tcp), udp: summarizeAgg(p.connect.udp) },
    rtt: { http: summarizeAgg(p.rtt.http), tcp: summarizeAgg(p.rtt.tcp), udp: summarizeAgg(p.rtt.udp) },
  });
  return { warmup: summarizePhase(latencyStats.warmup), steady: summarizePhase(latencyStats.steady) };
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------------- Connection openers (one per protocol) ----------------
// Ping/pong heartbeat interval used for ongoing RTT sampling (also doubles
// as the NAT keepalive for TCP/UDP).
const PING_INTERVAL_MS = 2000;

function openHttpConn(host, port) {
  const t0 = Date.now();
  // POST (not GET) so we can stream a chunked request body — this lets us
  // send PING lines to the server over the same long-lived connection that
  // is also streaming the /keepalive response back, giving us a duplex
  // channel for RTT measurement without opening a second socket.
  const req = http.request({ host, port, path: '/keepalive', method: 'POST', agent: false }, (res) => {
    const connectMs = Date.now() - t0;
    recordLatency('connect', 'http', connectMs);
    state.active.http++;
    state.succeeded.http++;

    let buf = '';
    res.on('data', (chunk) => {
      buf += chunk.toString('utf8');
      let idx;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (line.startsWith('PONG:')) {
          const ts = parseInt(line.slice(5), 10);
          if (!isNaN(ts)) recordLatency('rtt', 'http', Date.now() - ts);
        }
      }
    });
    res.on('end', () => onClosed('http', req));
  });
  req.on('error', () => onFailed('http', req));
  req.on('socket', (socket) => socket.setKeepAlive(true, 30000));
  // Node doesn't flush a chunked request's headers until the first write()
  // or end() call. Without this, TTFB would be inflated by however long it
  // takes the first heartbeat tick to fire (up to PING_INTERVAL_MS), and
  // could even get mis-bucketed into the wrong phase. Flush immediately so
  // the measured latency reflects real network time.
  req.flushHeaders();

  const hb = setInterval(() => {
    try { req.write(`PING:${Date.now()}\n`); } catch (e) {}
  }, PING_INTERVAL_MS);
  req.on('close', () => clearInterval(hb));
  sockets.http.push(req);
}

function openTcpConn(host, port) {
  const t0 = Date.now();
  const socket = net.connect(port, host, () => {
    const connectMs = Date.now() - t0;
    recordLatency('connect', 'tcp', connectMs);
    state.active.tcp++;
    state.succeeded.tcp++;
  });
  socket.on('error', () => onFailed('tcp', socket));
  socket.on('close', () => onClosed('tcp', socket));

  // The server echoes back exactly what we send, so a PING line sent here
  // comes back as the same PING line — that round trip is our RTT sample.
  let buf = '';
  socket.on('data', (data) => {
    buf += data.toString('utf8');
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (line.startsWith('PING:')) {
        const ts = parseInt(line.slice(5), 10);
        if (!isNaN(ts)) recordLatency('rtt', 'tcp', Date.now() - ts);
      }
    }
  });

  const hb = setInterval(() => {
    if (socket.destroyed) { clearInterval(hb); return; }
    try { socket.write(`PING:${Date.now()}\n`); } catch (e) {}
  }, PING_INTERVAL_MS);
  socket.on('close', () => clearInterval(hb));
  sockets.tcp.push(socket);
}

function openUdpConn(host, port) {
  const socket = dgram.createSocket('udp4');
  let connected = false;
  let connectTimer = null;

  socket.on('error', () => { onFailed('udp', socket); clearTimeout(connectTimer); });

  // UDP has no handshake, so the first echoed reply IS the connect-latency
  // sample; every reply after that is an ongoing RTT sample.
  socket.on('message', (msg) => {
    const line = msg.toString('utf8').trim();
    if (!line.startsWith('PING:')) return;
    const ts = parseInt(line.slice(5), 10);
    if (isNaN(ts)) return;
    const rtt = Date.now() - ts;
    if (!connected) {
      connected = true;
      clearTimeout(connectTimer);
      recordLatency('connect', 'udp', rtt);
      state.active.udp++;
      state.succeeded.udp++;
    } else {
      recordLatency('rtt', 'udp', rtt);
    }
  });

  const send = () => { try { socket.send(`PING:${Date.now()}`, port, host); } catch (e) {} };
  send();
  // If no echo arrives at all, treat the connection as failed rather than
  // silently counting it as successful (UDP gives no ack otherwise).
  connectTimer = setTimeout(() => {
    if (!connected) { onFailed('udp', socket); try { socket.close(); } catch (e) {} }
  }, 5000);

  const hb = setInterval(send, PING_INTERVAL_MS);
  socket.on('close', () => clearInterval(hb));
  sockets.udp.push(socket);
}

const openers = { http: openHttpConn, tcp: openTcpConn, udp: openUdpConn };

function onClosed(type, handle) {
  const arr = sockets[type];
  const idx = arr.indexOf(handle);
  if (idx >= 0) arr.splice(idx, 1);
  if (state.active[type] > 0) state.active[type]--;
}

function onFailed(type, handle) {
  state.failed[type]++;
  onClosed(type, handle);
}

// ---------------- Warmup ramp-up ----------------
async function rampUp(gen, type, host, port, targetCount, totalSteps, stepIntervalMs) {
  const perStep = Math.max(1, Math.ceil(targetCount / totalSteps));
  let opened = 0;
  while (opened < targetCount && gen === currentGeneration) {
    const batch = Math.min(perStep, targetCount - opened);
    for (let i = 0; i < batch; i++) openers[type](host, port);
    opened += batch;
    await sleep(stepIntervalMs);
  }
}

async function runTest(cfg) {
  // Tear down any previous run (e.g. Start clicked again without Stop first)
  // before opening anything new — see the comment on currentGeneration above.
  const gen = ++currentGeneration;
  closeAllSockets();

  state = freshState();
  latencyStats = freshLatencyStats();
  state.config = cfg;
  state.startedAt = Date.now();
  state.target = { http: cfg.httpTarget || 0, tcp: cfg.tcpTarget || 0, udp: cfg.udpTarget || 0 };

  const warmupMs = (cfg.warmupSeconds || 10) * 1000;
  const steps = Math.max(1, cfg.warmupSteps || 10);
  const stepIntervalMs = warmupMs / steps;

  state.phase = 'warmup';
  const jobs = [];
  if (cfg.httpTarget > 0) jobs.push(rampUp(gen, 'http', cfg.host, cfg.httpPort, cfg.httpTarget, steps, stepIntervalMs));
  if (cfg.tcpTarget > 0) jobs.push(rampUp(gen, 'tcp', cfg.host, cfg.tcpPort, cfg.tcpTarget, steps, stepIntervalMs));
  if (cfg.udpTarget > 0) jobs.push(rampUp(gen, 'udp', cfg.host, cfg.udpPort, cfg.udpTarget, steps, stepIntervalMs));

  await Promise.all(jobs);
  if (gen === currentGeneration) state.phase = 'steady';
}

function stopTest() {
  currentGeneration++; // invalidates any in-flight ramp-up loop immediately
  closeAllSockets();
  state.active = { http: 0, tcp: 0, udp: 0 };
  state.phase = 'stopped';
}

// ---------------- Control panel HTTP server ----------------
function readBody(req) {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => resolve(body));
  });
}

const controlServer = http.createServer(async (req, res) => {
  const parsed = url.parse(req.url);

  if (parsed.pathname === '/api/start' && req.method === 'POST') {
    const body = await readBody(req);
    let cfg;
    try { cfg = JSON.parse(body); } catch (e) { res.writeHead(400); res.end('bad json'); return; }
    runTest(cfg); // fire and forget, runs in the background
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  if (parsed.pathname === '/api/stop' && req.method === 'POST') {
    stopTest();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  if (parsed.pathname === '/api/state') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ...state, latency: latencySnapshot() }));
    return;
  }

  if (parsed.pathname === '/events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    const iv = setInterval(() => res.write(`data: ${JSON.stringify({ ...state, latency: latencySnapshot() })}\n\n`), 1000);
    req.on('close', () => clearInterval(iv));
    return;
  }

  let filePath = parsed.pathname === '/' ? '/panel.html' : parsed.pathname;
  filePath = path.join(publicDir, filePath);
  if (!filePath.startsWith(publicDir)) { res.writeHead(403); res.end('forbidden'); return; }
  fs.readFile(filePath, (err, data) => {
    if (err) { res.writeHead(404); res.end('not found'); return; }
    const ext = path.extname(filePath);
    const type = ext === '.html' ? 'text/html' : ext === '.js' ? 'application/javascript' : 'text/plain';
    res.writeHead(200, { 'Content-Type': type });
    res.end(data);
  });
});

controlServer.listen(CONTROL_PORT, CONTROL_HOST, () => {
  console.log(`[client control ui] listening on ${CONTROL_HOST}:${CONTROL_PORT}`);
});
