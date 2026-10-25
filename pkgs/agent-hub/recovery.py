"""Local, deterministic recovery for a confirmed Codex capacity failure."""
import json
import time

import bridge


class CapacityRecovery:
    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        with hub.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS capacity_recovery(agent_id TEXT PRIMARY KEY, connection_id TEXT, value TEXT)")
            self.records = {row["agent_id"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM capacity_recovery")}

    def save(self, agent_id, record):
        # Persist cooldown before delivering input. A restart cannot replay it.
        with self.hub.db() as db:
            db.execute("INSERT OR REPLACE INTO capacity_recovery VALUES(?,?,?)",
                       (agent_id, record["connection_id"], json.dumps(record)))
        self.records[agent_id] = record

    def manual(self, agent_id, paused, connection_id=None):
        record = self.records.get(agent_id)
        if not record and paused and connection_id:
            record = {'connection_id':connection_id, 'attempts':0, 'status':'paused', 'next_attempt':0}
        if record:
            self.save(agent_id, {**record, "paused": paused, "pause_seen": False, "active": False})

    def observe(self, connection, agent):
        agent.pop("recovery", None)
        # Each host owns its local recovery. Federated snapshots never send input.
        if connection["kind"] != "local":
            return
        with self.hub.action_lock:
            self._observe(connection, agent)

    def _observe(self, connection, agent):
        now, key = self.clock(), agent["id"]
        record = dict(self.records.get(key, {}))
        if record.get("paused"):
            if agent.get("activity_confirmed") and record.get("pause_seen"):
                record["paused"] = False  # The user resumed work in the terminal.
                self.save(key, record)
            elif not agent.get("activity_confirmed") and not record.get("pause_seen"):
                record["pause_seen"] = True
                self.save(key, record)
        if not agent.get("capacity_error"):
            if record.get("active"):
                if agent.get("activity_confirmed") or agent.get("state") in ("done", "waiting") or not agent.get("live") or now - record.get("last_attempt", now) > 30:
                    record["active"] = False
                    self.save(key, record)
                    if agent.get("activity_confirmed") and record.get("attempts"):
                        self.hub.event("recovery", f'{agent["title"]} on {connection["label"]} resumed after a capacity retry', key)
                else:
                    agent["recovery"] = {"kind": "capacity", "status": "checking", "attempts": record.get("attempts", 0), "next_retry": record.get("next_attempt", 0)}
            return
        if not record.get("active"):
            record.update(connection_id=connection["id"], active=True, since=now, attempts=0,
                          next_attempt=max(now + 2, record.get("last_attempt", 0) + 5), status="retrying")
            self.save(key, record)
        if record.get("paused"):
            agent["recovery"] = {"kind": "capacity", "status": "paused", "attempts": record["attempts"], "next_retry": 0}
            return
        if now >= record["next_attempt"]:
            record.update(last_attempt=now, next_attempt=now + min(20, 5 * 2 ** min(record["attempts"], 2)))
            self.save(key, record)
            try:
                bridge.retry_capacity(connection, agent)
            except bridge.BridgeError as error:
                record.update(status="waiting", error=str(error), next_attempt=now + 2)
            else:
                record.update(status="retrying", attempts=record["attempts"] + 1, error="")
                self.hub.event("recovery", f'Capacity retry {record["attempts"]}: sent continue to {agent["title"]} on {connection["label"]}', key)
            self.save(key, record)
        agent["recovery"] = {"kind": "capacity", "status": record["status"], "attempts": record["attempts"],
                             "next_retry": record["next_attempt"], "note": record.get("error", "")}

    def prune(self, connection_id, agent_ids):
        with self.hub.action_lock:
            stale = [key for key, record in self.records.items() if record["connection_id"] == connection_id and key not in agent_ids]
            if not stale:
                return
            with self.hub.db() as db:
                db.executemany("DELETE FROM capacity_recovery WHERE agent_id=?", [(key,) for key in stale])
            for key in stale:
                self.records.pop(key, None)
