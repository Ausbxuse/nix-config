"use strict";
(function (root) {
  function readings(connection, now = Date.now() / 1000, provider = "codex") {
    const usage = provider === "claude" ? connection.claude_usage : connection.usage;
    const reported = provider === "claude" ? usage?.limits : usage?.weekly;
    const limits = Array.isArray(reported) ? reported.slice(0, 16) : [];
    const stale = connection.status !== "online" || usage?.stale !== false || !Number.isFinite(usage?.observed_at) || now - usage.observed_at > 180;
    const rows = limits.filter(item => item && Number.isFinite(item.used_percent) && item.used_percent >= 0).map(item => {
      const reset = Number.isFinite(item.resets_at) && item.resets_at > 0 ? item.resets_at : null;
      const expired = reset !== null && reset <= now;
      const remaining = Math.max(0, Math.min(100, 100 - item.used_percent));
      return { id: item.id, label: item.label || (provider === "claude" ? "Claude" : "Codex"),
        period: item.window_minutes === 300 ? "5h" : "weekly", remaining, reset, expired, stale,
        value: expired ? "Awaiting update" : `${Number(remaining.toFixed(1))}% left`,
        level: expired || stale ? "stale" : remaining <= 10 ? "low" : remaining <= 25 ? "warning" : "normal" };
    });
    return { rows, stale, status: usage?.status || "unavailable", reason: usage?.reason || "", observed: usage?.observed_at || 0,
      note: connection.status !== "online" ? "Host offline" : stale && rows.length ? "Last known usage" : "" };
  }
  function resetTime(epoch) {
    if (!epoch) return "Reset time not reported";
    return "Resets " + new Date(epoch * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", hourCycle: "h23", minute: "2-digit", timeZoneName: "short",
    });
  }
  function context(snapshot, agent = null) {
    const connections = snapshot?.connections || [], supervisor = snapshot?.supervisor || {};
    const owner = supervisor.owner || supervisor.node_id;
    const connection = agent ? connections.find(item => item.id === agent.connection_id) : owner ?
      connections.find(item => item.id === owner || item.peer_id === owner) :
      connections.find(item => item.kind === "local");
    return { connection, provider: (agent?.runtime || supervisor.provider) === "claude" ? "claude" : "codex" };
  }
  function weekly(connection, now, provider = "codex") {
    const usage = readings(connection || {}, now, provider), rows = usage.rows.filter(row => row.period === "weekly");
    // The shared weekly pool is not a sum of model-specific or five-hour limits.
    const row = rows.find(item => provider === "claude" ? item.id === "seven_day" : /^codex(?::|$)/.test(item.id)) ||
      rows.find(item => item.label === (provider === "claude" ? "Claude" : "Codex")) || (rows.length === 1 ? rows[0] : null);
    const value = !row || row.expired ? "—" : row.remaining > 0 && row.remaining < 1 ? "<1%" : `${Math.floor(row.remaining)}%`;
    return { ...usage, row, value, level: row?.level || "stale" };
  }
  function detailSources(snapshot, connection, provider, now) {
    const other = provider === "claude" ? "codex" : "claude";
    const active = new Set((snapshot?.agents || []).filter(agent => agent.runtime === other && agent.live).map(agent => agent.connection_id));
    const alternatives = (snapshot?.connections || []).filter(item => active.has(item.id) ||
      readings(item, now, other).rows.some(row => !row.expired));
    if (!alternatives.length && connection) alternatives.push(connection);
    return [...(connection ? [{connection, provider}] : []), ...alternatives.map(item => ({connection:item, provider:other}))];
  }
  function headerSources(snapshot, agent, now) {
    const { connection, provider } = context(snapshot, agent), other = provider === "claude" ? "codex" : "claude";
    const connections = snapshot?.connections || [];
    const active = new Set((snapshot?.agents || []).filter(item => item.runtime === other && item.live).map(item => item.connection_id));
    const candidates = [...new Set([connection, ...connections.filter(item => active.has(item.id)), ...connections].filter(Boolean))];
    const reports = new Map(candidates.map(item => [item, weekly(item, now, other)]));
    const alternative = candidates.find(item => { const row = reports.get(item).row; return row && !row.expired && !row.stale; }) ||
      candidates.find(item => active.has(item.id)) || candidates.find(item => reports.get(item).row) || connection;
    // Keep the recipient's own account, even when unavailable. Only the other
    // provider may use a different host; never combine independent allowances.
    return ["codex", "claude"].map(name => ({provider:name, connection:name === provider ? connection : alternative}));
  }
  const api = { readings, resetTime, context, weekly, detailSources, headerSources };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HubUsage = api;
})(globalThis);
