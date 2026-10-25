"use strict";
(function (root) {
  const elapsed = (seconds) => seconds < 60 ? `${Math.max(0, Math.floor(seconds))}s` : `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  function supervisor({ sending, job, busy, connected, available, since, now }) {
    if (!connected) return { kind: "offline", title: "Reconnecting to the hub", note: "Progress is unavailable. Your request will not be sent again automatically." };
    if (!available) return { kind: "offline", title: "ARIA unavailable", note: "Waiting for its host to reconnect." };
    if (sending) return { kind: "active", title: "Sending your message", since: sending.created, note: "" };
    if (!busy && job?.status !== "running") return null;
    const started = job?.started_at || since || now;
    const phase = { gathering: "Reading agent updates", thinking: "Thinking", responding: "Responding", acting: "Carrying out your request" }[job?.phase] || "Working on your request";
    const unconfirmed = now - started >= 90 && job?.status !== "running";
    return { kind: unconfirmed ? "quiet" : "active", title: unconfirmed ? "Checking progress" : phase, since: started,
      note: unconfirmed ? "Waiting for a current job status. Your message will not be sent again." : "" };
  }
  function agent({ agent, receipt, observation, connected, now }) {
    if (!connected || !agent?.connection_online) return { kind: "offline", title: "Connection interrupted", note: "Showing the last received output. Reconnecting…" };
    if (!agent.live) return { kind: "idle", title: "Agent exited", note: "Its last output is still available." };
    if (receipt?.status === "sending") return { kind: "active", title: "Sending your instruction", since: receipt.created, note: "" };
    const recovery = agent.recovery;
    if (recovery?.kind === 'capacity' && !agent.activity_confirmed) {
      if (recovery.status === 'paused') return {kind:'attention',title:'Capacity retries paused',note:'Send an instruction to resume.'};
      if (recovery.status === 'waiting') return {kind:'quiet',title:'Capacity retry waiting',note:recovery.note || 'Waiting for an empty terminal input.'};
      if (recovery.status === 'checking') return {kind:'active',title:'Checking for activity',note:'Continue was sent. Waiting for the agent.'};
      return {kind:'active',title:`Capacity · retry in ${Math.max(0,Math.ceil(recovery.next_retry-now))}s`,note:recovery.attempts?`Sent continue ${recovery.attempts === 1 ? 'once' : recovery.attempts+' times'}. Waiting for activity.`:'The supervisor will send continue.'};
    }
    if (agent.state === "waiting" || agent.state === "blocked") return { kind: "attention", title: agent.state === "waiting" ? "Needs your input" : "Agent needs attention", note: agent.summary || "Open tmux to check the prompt." };
    if (agent.state === "unknown") return { kind: "quiet", title: "Checking turn status", note: "" };
    if (agent.state === "running") {
      if (agent.activity_confirmed) return { kind: "active", title: agent.activity_phase || "Working", since: observation?.started || now, note: "" };
      const quiet = now - (observation?.changed || now);
      return { kind: quiet >= 90 ? "quiet" : "active", title: quiet >= 90 ? "No new output" : "Working", since: observation?.started || now,
        note: quiet >= 90 ? `Quiet for ${elapsed(quiet)}. The agent still reports working. Check raw output or request a pause.` : "" };
    }
    if (receipt?.status === "sent" && !receipt.responseSeen && !receipt.wasRunning) {
      const waiting = now - receipt.created;
      return { kind: waiting >= 30 ? "quiet" : "active", title: waiting >= 30 ? "No response yet" : "Waiting for the agent", since: receipt.created,
        note: waiting >= 30 ? "The instruction was delivered to tmux. Check raw output before sending it again." : "Instruction delivered. Waiting for activity…" };
    }
    return { kind: "idle", title: agent.state === "done" ? "Turn finished" : "Ready for your next instruction", note: "" };
  }
  const api = { elapsed, supervisor, agent };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HubActivity = api;
})(globalThis);
