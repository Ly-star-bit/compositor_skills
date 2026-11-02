"""PhotoCraft's headless engine for pcraft_retouch.py: find (or fetch and verify) `photocraft-cli`, and talk to
`photocraft-cli serve` over stdio, one JSON line per request. No window, no GPU: serve is the engine alone."""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import urllib.request

# The command grammar still moves between releases, so the skill runs the release it was written and tested against.
VERSION = "0.19.0"
ZIP_NAME = f"photocraft-cli-{VERSION}-macos-universal.zip"
ZIP_URL = f"https://github.com/storytold/photocraft/releases/download/v{VERSION}/{ZIP_NAME}"
ZIP_SHA256 = "69f321313156728d4a372579dac9b443bc2f9494d3824d3186f536c98aa4388c"
CACHE_FOLDER = os.path.expanduser("~/Library/Caches/photocraft-retouch")
CACHED_CLI = os.path.join(CACHE_FOLDER, f"photocraft-cli-{VERSION}", f"photocraft-cli-{VERSION}-macos-universal",
                          "photocraft-cli")


class ServeError(Exception):
    pass


def cli_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = out.split()
    return parts[1] if len(parts) > 1 and parts[0] == "photocraft-cli" else None


def find_cli(say, warn):
    """PHOTOCRAFT_CLI, then `photocraft-cli` on PATH, then the cached release; else download the pinned release
    (SHA-256 checked, then Gatekeeper's notarization check) into ~/Library/Caches/photocraft-retouch/."""
    for path, origin in ((os.environ.get("PHOTOCRAFT_CLI"), "PHOTOCRAFT_CLI"), (shutil.which("photocraft-cli"), "PATH"),
                         (CACHED_CLI if os.path.isfile(CACHED_CLI) else None, "cache")):
        if not path:
            continue
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            raise ServeError(f"{origin} points at {path}, which is not an executable photocraft-cli")
        version = cli_version(path)
        if version != VERSION:
            warn(f"photocraft-cli {version or '(unknown version)'} from {origin}; this skill is tested with {VERSION}, "
                 "and command parameters change between releases", once=True)
        return path
    return download_cli(say, warn)


def download_cli(say, warn):
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
            raise ServeError(f"{ZIP_NAME}: SHA-256 {digest.hexdigest()} is not the release's {ZIP_SHA256}; not used")
        unpacked = os.path.join(work, "x")
        subprocess.run(["ditto", "-x", "-k", archive, unpacked], check=True, capture_output=True)
        binary = os.path.join(unpacked, f"photocraft-cli-{VERSION}-macos-universal", "photocraft-cli")
        check = subprocess.run(["spctl", "--assess", "--type", "install", "-vv", binary], capture_output=True, text=True,
                               timeout=120)
        verdict = (check.stdout + check.stderr).strip().replace("\n", "; ")
        if check.returncode != 0:
            raise ServeError(f"Gatekeeper did not accept the downloaded photocraft-cli ({verdict}). Its notarization "
                             "is checked online the first time: with no network, try again once online")
        target = os.path.dirname(os.path.dirname(CACHED_CLI))
        shutil.rmtree(target, ignore_errors=True)
        os.replace(unpacked, target)
        say(f"[OK] photocraft-cli {VERSION} cached at {CACHED_CLI} ({verdict})")
        return CACHED_CLI
    except (OSError, subprocess.SubprocessError) as error:
        raise ServeError(f"could not download photocraft-cli: {error}. Install it yourself from {ZIP_URL} "
                         "and set PHOTOCRAFT_CLI to the unzipped photocraft-cli") from error
    finally:
        shutil.rmtree(work, ignore_errors=True)


class Serve:
    """One `photocraft-cli serve` session. `root` is the only folder it may read or write."""

    def __init__(self, cli, root):
        self.proc = subprocess.Popen(
            [cli, "serve", "--automation-read-root", root, "--automation-write-root", root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.replies = queue.Queue()
        self.stderr = []
        self.next_id = 1
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            if line.strip():
                self.replies.put(json.loads(line))
        self.replies.put(None)

    def _drain(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())
            del self.stderr[:-20]

    def call(self, method, params=None, timeout=900):
        rid = self.next_id
        self.next_id += 1
        try:
            self.proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params or {}}) + "\n")
            self.proc.stdin.flush()
        except OSError as error:
            raise ServeError(f"photocraft-cli serve stopped ({error}): {' / '.join(self.stderr[-3:])}") from error
        while True:
            try:
                reply = self.replies.get(timeout=timeout)
            except queue.Empty:
                raise ServeError(f"photocraft-cli serve gave no reply to {method} in {timeout}s") from None
            if reply is None:
                raise ServeError(f"photocraft-cli serve exited during {method}: {' / '.join(self.stderr[-3:])}")
            if reply.get("id") == rid:
                break
        if not reply.get("ok"):
            raise ServeError(f"{method} {json.dumps(params, ensure_ascii=False)[:240]}: {reply.get('error')}")
        return reply.get("result")

    def run(self, command, params=None):
        return self.call("engine.execute", {"command": command, "params": params or {}, "wait": True})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
