"""Checks the PhotoCraft link on this Mac: run it while PhotoCraft is open, started by Compositor 工具's 联动启动
(or with --control and the PHOTOCRAFT_CONTROL_* variables). It tries each step pcraft_retouch.py takes on a scratch
document of its own, made in the folder PhotoCraft was given and deleted after, and prints what happened.

    python3 link_check.py            (python3 -I is fine too)

Nothing of the user's is saved, closed or changed: their documents only stay open; the one in front is in front
again after."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from photocraft import AppLink, Serve, ServeError, find_cli, link_settings, outside_root  # noqa: E402

NAME = ".pcraft-link-check.pcraft"


def step(label, act):
    try:
        result = act()
        print(f"  ok: {label}")
        return result
    except (ServeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"  FAILED: {label}: {error}")
        raise SystemExit(1) from None


def need(value, why):
    if not value:
        raise ValueError(why)
    return value


def main():
    settings = link_settings()
    if not settings:
        print("No link set up: start PhotoCraft from Compositor 工具 (PhotoCraft tab › 联动启动 PhotoCraft), or set "
              "PHOTOCRAFT_CONTROL_PORT and PHOTOCRAFT_CONTROL_TOKEN_FILE for a PhotoCraft started with --control.")
        return 1
    root = os.path.expanduser(settings.get("root") or "")
    if not os.path.isdir(root):
        print(f"The link names no folder PhotoCraft may use (root: {root or 'none'}); start it from Compositor 工具.")
        return 1
    print(f"PhotoCraft link: port {settings['port']}, folder {root}")
    app = step("connect and authenticate", lambda: AppLink(settings))
    session = step("list the open documents (session.inspect)", app.session)
    for doc in session.get("documents", []):
        where = doc.get("path") or ""
        reach = outside_root(os.path.join(root, where), root) if where else "never saved"
        print(f"     [{doc.get('index')}] {doc.get('name')}  dirty={doc.get('dirty')}  path={where}"
              + (f"  (the link leaves it alone: {reach})" if reach else ""))
    contents = step("which tabs are a smart object's contents (engine.commands, one tab without a file at a time)",
                    lambda: app.contents_tabs(session))
    print(f"     {', '.join(doc.get('name') or '?' for doc in contents) or 'none'}")
    front = session.get("active")
    path = os.path.join(root, NAME)
    try:
        step("a scratch document (file.new)", lambda: app.run("file.new", {"width": 64, "height": 64}))
        step(f"saved into the folder (app.save {{path}}): {path}", lambda: app.call("app.save", {"path": path}))
        doc = step("found again by its file", lambda: need(app.document(path), "not among the open documents"))
        step("an unsaved change on it", lambda: app.run("layer.new.layer", {"name": "unsaved"}))
        dirty = app.document(path)
        print(f"     dirty after the change: {dirty.get('dirty') if dirty else '?'}")
        step("File › Save with no path (app.save {}) — what pcraft_retouch does first",
             lambda: app.save(app.document(path)))
        saved = app.document(path)
        step("...and it has no unsaved changes now", lambda: need(saved and not saved.get("dirty"), f"still {saved}"))
        print("     the headless engine adds a layer to the file, as pcraft_retouch does")
        serve = Serve(find_cli(print, print), root)
        try:
            serve.call("doc.open", {"path": path})
            serve.call("engine.execute", {"command": "layer.new.layer", "params": {"name": "added on disk"}})
            serve.call("doc.save", {"path": path})
        finally:
            serve.close()
        step("loaded again: opened from the file, then the old tab closed (app.open, file.close, document.move)",
             lambda: need(app.reload(path), "it was no longer open"))
        again = app.document(path)
        names = [layer.get("name") for layer in (app.run("document.inspect", {"document": again["index"]}) or {}).get("layers", [])]
        step(f"...in its tab ({again['index']} as before: {doc['index']}), with the layer added on disk",
             lambda: need(again["index"] == doc["index"] and "added on disk" in names, f"layers {names}"))
        now = app.session().get("active")
        print(f"     the document in front: {now} (it was {front} before the check, the scratch one {doc['index']})")
    finally:
        left = app.document(path)
        if left:
            app.run("file.close", {"document": left["index"]})
        if front is not None and front < len(app.session().get("documents", [])):
            app.run("document.activate", {"document": front})
        if os.path.exists(path):
            os.remove(path)
        app.close()
    print("All steps work: pcraft_retouch.py will save a document PhotoCraft has open and load it again by itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
