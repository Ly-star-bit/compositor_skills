"""PhotoCraft's headless engine for pcraft_retouch.py: find (or fetch and verify) `photocraft-cli`, and talk to
`photocraft-cli serve` over stdio, one JSON line per request. No window, no GPU: serve is the engine alone."""
import hashlib
import json
import os
import queue
import shutil
import socket
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


DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"} | {
    f"{port}{n}" for port in ("COM", "LPT") for n in "0123456789¹²³"}


def refused_name(name):
    """Why the engine's automation refuses `name` as one part of a path (its rules hold on every platform: a
    document can be opened elsewhere), or None."""
    if not name or name in (".", ".."):
        return "an empty, `.` or `..` name"
    if ":" in name or "\\" in name:
        return "a `:` or `\\` in the name"  # Finder shows a `:` as `/`
    if name.endswith((".", " ")):
        return "a name ending in a dot or space"
    if name.split(".")[0].rstrip().upper() in DEVICE_NAMES:
        return "a Windows device name (con, aux, nul, com1…)"
    return None


def outside_root(path, root):
    """Why the engine's automation, given the folder `root`, refuses the absolute `path`, or None: it must be in that
    folder, as given or as resolved, and every name below it allowed (see refused_name). No root known: None, and
    the app decides."""
    if not root:
        return None
    root = os.path.expanduser(root)
    for base in dict.fromkeys((os.path.abspath(root), os.path.realpath(root))):
        if os.path.commonpath([path, base]) == base:
            for name in os.path.relpath(path, base).split(os.sep):
                if refused_name(name):
                    return f"{refused_name(name)}: {name}"
            return None
    return f"not in {root}"


class Serve:
    """One `photocraft-cli serve` session. `root` is the only folder it may read or write."""

    def __init__(self, cli, root):
        # PHOTOCRAFT_CONTROL_PORT (set for the app's control port, see AppLink) would turn serve into a TCP server.
        env = {k: v for k, v in os.environ.items() if not k.startswith("PHOTOCRAFT_CONTROL_")}
        self.proc = subprocess.Popen(
            [cli, "serve", "--automation-read-root", root, "--automation-write-root", root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
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


# The PhotoCraft app itself, when it listens on its control port (`photocraft --control <port>`, which Compositor 工具
# starts it with): a document it has open is saved there before the script reads the file, and loaded again after.
LINK_FILE = os.path.expanduser("~/Library/Application Support/compositor-retouch/photocraft-link.json")


def link_settings():
    """Where the app's control port is: PHOTOCRAFT_CONTROL_PORT with PHOTOCRAFT_CONTROL_TOKEN or _TOKEN_FILE (and
    PHOTOCRAFT_AUTOMATION_READ_ROOT, for documents it lists by relative path), else the link file Compositor 工具
    writes when it starts PhotoCraft ({"port", "tokenFile", "root"}). None without either."""
    port = os.environ.get("PHOTOCRAFT_CONTROL_PORT")
    if port:
        return {"port": port, "token": os.environ.get("PHOTOCRAFT_CONTROL_TOKEN"),
                "tokenFile": os.environ.get("PHOTOCRAFT_CONTROL_TOKEN_FILE"),
                "root": os.environ.get("PHOTOCRAFT_AUTOMATION_READ_ROOT")}
    try:
        with open(LINK_FILE, encoding="utf-8") as f:
            link = json.load(f)
    except (OSError, ValueError):
        return None
    return link if isinstance(link, dict) and link.get("port") else None


class AppLink:
    """One connection to the PhotoCraft app's control port: the serve protocol's JSON lines after an `auth` frame.
    Automation may not run File › Revert (it reads by path), so loading a document again is closing it and opening the
    file, which the app's automation roots allow."""

    def __init__(self, settings, timeout=3):
        token = settings.get("token")
        if not token and settings.get("tokenFile"):
            with open(os.path.expanduser(settings["tokenFile"]), encoding="utf-8") as f:
                token = f.read().strip()
        self.root = settings.get("root")
        self.sock = socket.create_connection(("127.0.0.1", int(settings["port"])), timeout=timeout)
        self.replies = self.sock.makefile("r", encoding="utf-8")
        self.next_id = 0
        try:
            self.call("auth", {"token": token or ""}, timeout=timeout)
        except ServeError:
            self.close()
            raise

    def call(self, method, params=None, timeout=75):  # past the app's own 60 s, so its reason comes back
        self.next_id += 1
        self.sock.settimeout(timeout)
        try:
            self.sock.sendall((json.dumps({"id": self.next_id, "method": method, "params": params or {}}) + "\n").encode())
            for line in self.replies:
                reply = json.loads(line)
                if reply.get("id") == self.next_id:
                    break
            else:
                raise ServeError(f"PhotoCraft closed the control connection during {method}")
        except (OSError, ValueError) as error:
            raise ServeError(f"PhotoCraft {method}: {error}") from error
        if not reply.get("ok"):
            raise ServeError(f"PhotoCraft {method}: {reply.get('error')}")
        return reply.get("result")

    def run(self, command, params=None):
        return self.call("engine.execute", {"command": command, "params": params or {}})

    def session(self):
        return self.run("session.inspect") or {}

    def opened_from(self, doc, path):
        """Whether the open document `doc` ({index, name, path, dirty, …}) is the file `path`: the same file, as the
        Mac's disks don't tell `Portrait` from `portrait` (or the two ways of writing é) apart."""
        where = doc.get("path")
        if not where:
            return False
        if not os.path.isabs(where) and self.root:
            where = os.path.join(os.path.expanduser(self.root), where)
        try:
            return os.path.samefile(where, path)
        except OSError:  # one of them isn't there (any more)
            return os.path.realpath(where) == os.path.realpath(path)

    def tabs(self, path, session=None):
        """The open documents whose file is `path`: one tab, or more when automation opened it again (the app's own
        File › Open shows the tab it has instead)."""
        return [doc for doc in (session or self.session()).get("documents", []) if self.opened_from(doc, path)]

    def document(self, path):
        """The (first) open document whose file is `path`, or None."""
        return next(iter(self.tabs(path)), None)

    def contents_tabs(self, session=None):
        """The open tabs that are a smart object's contents (Edit Contents): closing the document they belong to cuts
        them loose, and their changes can't go back into it. Only the active document tells (Save Contents is enabled
        there), so each tab without a file is looked at in turn."""
        def saves_contents():
            items = self.call("engine.commands")
            items = items.get("commands", []) if isinstance(items, dict) else items or []
            return any(isinstance(item, dict) and item.get("id") == "layer.smartObjects.saveContents"
                       and item.get("enabled") for item in items)
        return [doc for doc in (session or self.session()).get("documents", [])
                if not doc.get("path") and self._on(doc["index"], saves_contents)]

    def _on(self, index, act):
        """`act()` with the document at `index` active, the active one active again after."""
        active = self.session().get("active")
        if active != index:
            self.run("document.activate", {"document": index})
        try:
            return act()
        finally:
            if active is not None and active != index:
                self.run("document.activate", {"document": active})

    def save(self, doc):
        """File › Save: the document back to its own .pcraft."""
        self._on(doc["index"], lambda: self.call("app.save", {}))

    def reload(self, path):
        """The document open from `path` (in one tab) loaded again from the file, in that tab: opened again, and only
        then the old one closed (it must have no unsaved changes: an engine close never asks). Whatever stops it
        before the old one is closed closes the new one again, so the tab is left as it was. False when it is no
        longer open."""
        session = self.session()
        tabs = self.tabs(path, session)
        if not tabs:
            return False
        if len(tabs) > 1:
            raise ServeError(f"it is open in {len(tabs)} tabs")
        if tabs[0].get("dirty"):
            raise ServeError("it has changes made in PhotoCraft while this ran")
        index, active, count = tabs[0]["index"], session.get("active"), len(session.get("documents", []))
        try:
            self.call("app.open", {"path": path})  # a second tab of the same file, after the others
            docs = self.session().get("documents", [])
            if len(docs) != count + 1 or not self.opened_from(docs[count], path):
                raise ServeError("it did not open again")
            if docs[index].get("dirty"):  # changed while the new one opened: keep it
                raise ServeError("it has changes made in PhotoCraft while this ran")
            self.run("file.close", {"document": index})
        except BaseException:
            self._close_new(count, path, active)
            raise
        try:  # loaded again; where its tab sits is all that's left
            if count - 1 != index:
                self.run("document.move", {"document": count - 1, "to": index})
            self._activate(active)
        except ServeError:
            pass
        return True

    def _close_new(self, count, path, active):
        """A reload stopped before the old tab was closed: the tab app.open added closed again, when it is there."""
        try:
            docs = self.session().get("documents", [])
            if len(docs) == count:
                return
            if len(docs) == count + 1 and self.opened_from(docs[count], path) and not docs[count].get("dirty"):
                self.run("file.close", {"document": count})
                self._activate(active)
                return
        except (OSError, ServeError):
            pass
        raise ServeError("PhotoCraft may have opened it again in a second tab and kept the old one: close the older "
                         "tab of it there (the one further left)")

    def _activate(self, index):
        if index is not None:
            self.run("document.activate", {"document": index})

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def connect_app():
    """The app's control port when it is set up and PhotoCraft listens there; else None, and why not when that is
    worth saying (nothing listening is PhotoCraft quit, or started the ordinary way: no reason)."""
    settings = link_settings()
    if not settings:
        return None, None
    try:
        return AppLink(settings), None
    except ConnectionRefusedError:
        return None, None
    except FileNotFoundError as error:
        return None, f"no token file {error.filename}: PhotoCraft wasn't started with the link"
    except (OSError, ServeError, ValueError) as error:
        return None, f"{error}"
