"""Read explicitly configured plan sources. Never replicate personal context."""
import json
from pathlib import Path
import shlex
import subprocess
import sys

# The same bounded reader runs locally or on a configured SSH host.
READER = """
import glob,json,os,stat,sys
config=json.loads(sys.argv[1])
result=[]
paths=sorted(glob.glob(os.path.expanduser(config['path'])))
files=[]
for path in paths:
    try:
        info=os.stat(path)
        if stat.S_ISREG(info.st_mode): files.append((info.st_mtime,path))
    except OSError: pass
files.sort(reverse=True)
for modified,path in files[:config['max_files']]:
    try:
        with open(path,'rb') as stream: data=stream.read(config['max_chars']*4+1)
        text=data.decode('utf-8',errors='replace')
        result.append(dict(path=path,modified_at=modified,text=text[:config['max_chars']],
                           truncated=len(text)>config['max_chars']))
    except OSError:
        result.append(dict(path=path,error='Cannot read this file'))
print(json.dumps(dict(documents=result,omitted_files=max(0,len(files)-len(result)))))
"""


def load_plans(state_dir, worker=False):
    if worker:
        return {"sources": [], "scope": "Personal plans are available only on the controller"}
    config_file = Path(state_dir) / "plans.json"
    if not config_file.exists():
        return {"sources": [], "scope": "No plan sources configured"}
    try:
        config = json.loads(config_file.read_text())
        sources = config["sources"]
        if not isinstance(sources, list) or len(sources) > 20 or any(not isinstance(s, dict) for s in sources):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        return {"sources": [], "error": "Cannot read plan source settings"}
    result = []
    budget = 60000
    for source in sources:
        entry = {"label": source.get("label", "Plans"), "host": source.get("host", "local")}
        result.append(entry)
        try:
            if budget <= 0:
                entry["error"] = "Context limit reached; this source was not read"
                continue
            path = source["path"]
            if not isinstance(path, str) or not path.startswith(("/", "~/")):
                raise ValueError()
            options = {"path": path, "max_files": min(30, max(1, int(source.get("max_files", 1)))),
                       "max_chars": min(budget, 16000, max(200, int(source.get("max_chars", 6000))))}
            args = [sys.executable, "-c", READER, json.dumps(options)]
            if source.get("host"):
                host = source["host"]
                if not isinstance(host, str) or host.startswith("-") or any(c.isspace() for c in host):
                    raise ValueError()
                args = ["ssh", "-a", "-o", "ForwardAgent=no", "-o", "BatchMode=yes",
                        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=3", "--",
                        host, shlex.join(["python3", *args[1:]])]
            response = subprocess.run(args, capture_output=True, text=True, timeout=8, check=True)
            value = json.loads(response.stdout)
            entry.update(value)
            if not entry["documents"]:
                entry["error"] = "No matching plan files"
            for document in entry["documents"]:
                text = document.get("text", "")
                if len(text) > budget:
                    document["text"] = text[:budget]
                    document["truncated"] = True
                budget -= len(document.get("text", ""))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            entry["error"] = "Plan source unavailable"
    return {"sources": result, "scope": "Configured sources only; not a complete inventory of all user plans"}
