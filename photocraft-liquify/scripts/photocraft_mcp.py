"""PhotoCraft's MCP server for pcraft_liquify.py: find (or fetch and verify) `photocraft-cli`, and talk to
`photocraft-cli mcp` over stdio as an MCP client. Headless: the engine alone, no window, no GPU.

When this skill sits beside photocraft-retouch (the usual place, inside compositor-retouch) that skill's photocraft.py
finds the command line tool, so both skills run the same release. Copied somewhere on its own, the copy of the pin
below is used."""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))

# The same pin as photocraft-retouch/scripts/photocraft.py (tests/check.py fails when the two differ).
VERSION = "0.19.0"
ZIP_NAME = f"photocraft-cli-{VERSION}-macos-universal.zip"
ZIP_URL = f"https://github.com/storytold/photocraft/releases/download/v{VERSION}/{ZIP_NAME}"
ZIP_SHA256 = "69f321313156728d4a372579dac9b443bc2f9494d3824d3186f536c98aa4388c"
CACHE_FOLDER = os.path.expanduser("~/Library/Caches/photocraft-retouch")
CACHED_CLI = os.path.join(CACHE_FOLDER, f"photocraft-cli-{VERSION}", f"photocraft-cli-{VERSION}-macos-universal",
                          "photocraft-cli")


class McpError(Exception):
    pass


def sibling_module():
    """photocraft-retouch's photocraft.py when that skill is installed beside this one, else None."""
    folder = os.environ.get("PHOTOCRAFT_RETOUCH_SCRIPTS") or os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                                                         "photocraft-retouch", "scripts")
    if not os.path.isfile(os.path.join(folder, "photocraft.py")):
        return None
    sys.path.insert(0, folder)
    try:
        import photocraft
        return photocraft
    except ImportError:
        return None
    finally:
        sys.path.remove(folder)


def cli_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = out.split()
    return parts[1] if len(parts) > 1 and parts[0] == "photocraft-cli" else None


def find_cli(say, warn):
    """PHOTOCRAFT_CLI, then `photocraft-cli` on PATH, then the cached release; else (macOS) download the pinned
    release, SHA-256 checked and then Gatekeeper's notarization check, into ~/Library/Caches/photocraft-retouch/."""
    sibling = sibling_module()
    if sibling is not None:
        try:
            return sibling.find_cli(say, warn)
        except sibling.ServeError as error:
            raise McpError(str(error)) from error
    for path, origin in ((os.environ.get("PHOTOCRAFT_CLI"), "PHOTOCRAFT_CLI"), (shutil.which("photocraft-cli"), "PATH"),
                         (CACHED_CLI if os.path.isfile(CACHED_CLI) else None, "cache")):
        if not path:
            continue
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            raise McpError(f"{origin} points at {path}, which is not an executable photocraft-cli")
        version = cli_version(path)
        if version != VERSION:
            warn(f"photocraft-cli {version or '(unknown version)'} from {origin}; this skill is tested with {VERSION}, "
                 "and command parameters change between releases")
        return path
    if sys.platform != "darwin":
        raise McpError("photocraft-cli not found. Install PhotoCraft's command line tool for this system from "
                       f"https://github.com/storytold/photocraft/releases/tag/v{VERSION} and either put "
                       "`photocraft-cli` on PATH or set PHOTOCRAFT_CLI to its full path")
    return download_cli(say)


def download_cli(say):
    os.makedirs(CACHE_FOLDER, exist_ok=True)
    say(f"[STEP] downloading {ZIP_NAME} (about 107 MB) from the PhotoCraft GitHub release, once")
    work = tempfile.mkdtemp(prefix="download-", dir=CACHE_FOLDER)
    try:
        archive = os.path.join(work, ZIP_NAME)
        digest = hashlib.sha256()
        with urllib.request.urlopen(ZIP_URL, timeout=120) as response, open(archive, "wb") as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != ZIP_SHA256:
            raise McpError(f"{ZIP_NAME}: SHA-256 {digest.hexdigest()} is not the release's {ZIP_SHA256}; not used")
        unpacked = os.path.join(work, "x")
        subprocess.run(["ditto", "-x", "-k", archive, unpacked], check=True, capture_output=True)
        binary = os.path.join(unpacked, f"photocraft-cli-{VERSION}-macos-universal", "photocraft-cli")
        check = subprocess.run(["spctl", "--assess", "--type", "install", "-vv", binary], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=120)
        verdict = (check.stdout + check.stderr).strip().replace("\n", "; ")
        if check.returncode != 0:
            raise McpError(f"Gatekeeper did not accept the downloaded photocraft-cli ({verdict}). Its notarization "
                           "is checked online the first time: with no network, try again once online")
        target = os.path.dirname(os.path.dirname(CACHED_CLI))
        shutil.rmtree(target, ignore_errors=True)
        os.replace(unpacked, target)
        say(f"[OK] photocraft-cli {VERSION} cached at {CACHED_CLI} ({verdict})")
        return CACHED_CLI
    except (OSError, subprocess.SubprocessError) as error:
        raise McpError(f"could not download photocraft-cli: {error}. Install it yourself from {ZIP_URL} "
                       "and set PHOTOCRAFT_CLI to the unzipped photocraft-cli") from error
    finally:
        shutil.rmtree(work, ignore_errors=True)


class Mcp:
    """One `photocraft-cli mcp` session on stdio. `root` is the only folder the engine may read or write."""

    def __init__(self, cli, root, timeout=600):
        # PHOTOCRAFT_CONTROL_* would make this a bridge to a running PhotoCraft: this skill never touches the app.
        environment = {k: v for k, v in os.environ.items() if not k.startswith("PHOTOCRAFT_CONTROL")}
        self.timeout = timeout
        self.log = tempfile.TemporaryFile()
        # The engine speaks UTF-8 whatever the system's own encoding is. Left to the system's (GBK on a Chinese
        # Windows), the first reply with a layer or file name outside ASCII cannot be decoded.
        self.process = subprocess.Popen(
            [cli, "mcp", "--automation-read-root", root, "--automation-write-root", root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, encoding="utf-8", errors="replace",
            env=environment)
        self.replies = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "photocraft-liquify", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _read(self):
        try:
            for line in self.process.stdout:
                self.replies.put(line)
        except (OSError, ValueError) as error:  # never leave the caller waiting on a reader that has stopped
            self.replies.put(error)
        finally:
            self.replies.put(None)

    def _send(self, message):
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except OSError as error:
            raise McpError(f"photocraft-cli mcp stopped ({error}): {self._stderr()}") from error

    def _stderr(self):
        try:
            self.log.seek(0)
            return self.log.read().decode("utf-8", "replace").strip()[-600:] or "no message"
        except (OSError, ValueError):
            return "no message"

    def request(self, method, params=None):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        while True:
            try:
                line = self.replies.get(timeout=self.timeout)
            except queue.Empty:
                raise McpError(f"photocraft-cli mcp gave no reply to {method} in {self.timeout}s") from None
            if isinstance(line, Exception):
                raise McpError(f"could not read photocraft-cli mcp's reply to {method}: {line}")
            if line is None:
                raise McpError(f"photocraft-cli mcp exited during {method}: {self._stderr()}")
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise McpError(f"{method}: {reply['error'].get('message', reply['error'])}")
                return reply["result"]

    def call(self, name, arguments=None):
        """One MCP tool call: the structured result, or the text content parsed as JSON when it is JSON."""
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [part.get("text", "") for part in result.get("content", []) if part.get("type") == "text"]
        if result.get("isError"):
            raise McpError(f"{name}: {' '.join(texts)[:600]}")
        if "structuredContent" in result:
            return result["structuredContent"]
        try:
            return json.loads(texts[0]) if texts else None
        except ValueError:
            return texts[0]

    def command(self, command_id, params=None):
        """An engine command (filter.liquify, layer.newAdjustmentLayer.curves, ...) through the command_run tool."""
        return self.call("command_run", {"id": command_id, "params": params or {}})

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        self.log.close()
