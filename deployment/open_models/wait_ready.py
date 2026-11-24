"""Order GPU initialization so vLLM memory profiling does not race another loader."""
import json
import sys
import time
import urllib.error
import urllib.request

port = int(sys.argv[1])
for _ in range(300):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            body = json.load(response)
        if body.get("status") == "ok" or body.get("ok") is True:
            print(f"Model at {port} ready", flush=True)
            break
    except (OSError, ValueError, urllib.error.URLError):
        pass
    time.sleep(2)
else:
    raise SystemExit(f"Model at {port} did not become ready")
