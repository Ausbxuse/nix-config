"""Create a native Codex branch without running a turn or changing the parent."""
import json
import queue
import subprocess
import threading
import time

from bridge import BridgeError


class AppServer:
    def __init__(self, executable="codex"):
        self.executable = executable

    def __enter__(self):
        self.process = subprocess.Popen([self.executable, "app-server"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.responses = queue.Queue()
        self.sequence = 0
        def read():
            try:
                for line in self.process.stdout:
                    try:
                        self.responses.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                self.responses.put(None)
        threading.Thread(target=read, daemon=True).start()
        return self

    def call(self, method, params):
        self.sequence += 1
        self.process.stdin.write(json.dumps({"id": self.sequence, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 25
        while True:
            if time.monotonic() >= deadline:
                raise BridgeError("Codex branch operation timed out. Refresh before retrying.")
            try:
                result = self.responses.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                raise BridgeError("Codex branch operation timed out. Refresh before retrying.")
            if result is None:
                raise BridgeError("Codex branch service stopped.")
            if result.get("id") != self.sequence:
                continue
            if "error" in result:
                raise BridgeError("Codex could not create this branch: " + result["error"].get("message", "unknown error"))
            return result["result"]

    def __exit__(self, *args):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def fork_message(message, client=None, executable="codex"):
    identity = message["fork"]
    with (client() if client else AppServer(executable)) as rpc:
        rpc.call("initialize", {"clientInfo": {"name": "agent_hub", "version": "1"}})
        thread = rpc.call("thread/read", {"threadId": identity["thread"], "includeTurns": True})["thread"]
        turns = thread.get("turns", [])
        selected = next((i for i, turn in enumerate(turns) if turn["id"] == identity["turn"]), None)
        if selected is None:
            raise BridgeError("The selected turn is no longer available.")
        turn = turns[selected]
        users = [item for item in turn.get("items", []) if item.get("type") == "userMessage"]
        text = "\n".join(item.get("text", "") for item in users[0].get("content", []) if item.get("type") == "text") if len(users) == 1 else None
        if text != message["text"]:
            raise BridgeError("This message cannot be separated from its turn. Choose another message.")
        # Fork through the preceding completed turn. For the first turn, fork
        # through it, then remove it from the NEW branch only.
        anchor = turns[selected - 1] if selected else turn
        if anchor.get("status") == "inProgress":
            raise BridgeError("Wait for this turn to finish before branching.")
        branch = rpc.call("thread/fork", {"threadId": identity["thread"], "lastTurnId": anchor["id"]})["thread"]
        branch_id = branch["id"]
        if branch_id == identity["thread"]:
            raise BridgeError("Codex did not create a separate branch.")
        expected = [item["id"] for item in turns[:selected or 1]]
        if [item["id"] for item in branch.get("turns", [])] != expected:
            raise BridgeError("Codex did not preserve the requested branch boundary. The new agent was not started.")
        if not selected:
            rpc.call("thread/rollback", {"threadId": branch_id, "numTurns": 1})
        return branch_id
