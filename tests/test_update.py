"""The Update button: finding a new release, and installing it like the old one was installed.

makepkg, pkexec and systemctl are faked (they log what they were asked to do),
and GitHub is stubbed: nothing here touches the network or the system.
"""
import json
import os
import stat
import sys
import tarfile
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from common import ROOT, SRC, Checks, fake_pactl, load  # noqa: E402

try:
    import PySide6  # noqa: F401
except ImportError:
    print("SKIP PySide6 not installed")
    sys.exit(0)

tmp = fake_pactl("full")
json.dump({}, open(os.environ["FAKE_PACTL_STATE"], "w"))
gui = load(os.path.join(SRC, "nova-chatmix-gui.py"), "nova_chatmix_gui_update_test")
svc = gui.svc
gui.UPDATE_CACHE = os.path.join(tmp, "cache", "update.json")
check = Checks()
NEW = "9.9.9"

# --- fake tools
fakes = os.path.join(tmp, "fakebin")
os.makedirs(fakes)
LOG = os.path.join(tmp, "tools.log")


def tool(name, body):
    path = os.path.join(fakes, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho \"" + name + " $*\" >> " + LOG + "\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


tool("makepkg", 'grep -q "^pkgver=' + NEW + '$" PKGBUILD || { echo "wrong pkgver" >&2; exit 3; }\n'
     'test -f nova-chatmix-' + NEW + '.tar.gz || { echo "no tarball" >&2; exit 4; }\n'
     'touch nova-chatmix-' + NEW + '-1-any.pkg.tar.zst nova-chatmix-debug-' + NEW + '-1-any.pkg.tar.zst')
tool("pkexec", 'exit ${FAKE_PKEXEC_RC:-0}')
tool("systemctl", "exit 0")
os.environ["PATH"] = fakes + os.pathsep + os.environ["PATH"]


def log():
    try:
        return open(LOG).read().splitlines()
    except OSError:
        return []


# --- a release tarball, built like CI builds it
def make_release(version, claims=None):
    base = os.path.join(tmp, f"rel-{version}-{claims}")
    top = os.path.join(base, f"nova-chatmix-{version}")
    os.makedirs(os.path.join(top, "src"))
    os.makedirs(os.path.join(top, "packaging", "aur"))
    for f in ("nova-chatmix.py", "nova-chatmix-gui.py"):
        text = open(os.path.join(SRC, f)).read()
        if f == "nova-chatmix.py":
            text = text.replace(f'__version__ = "{svc.__version__}"', f'__version__ = "{claims or version}"')
        open(os.path.join(top, "src", f), "w").write(text)
    for f in ("PKGBUILD", "nova-chatmix.install"):
        open(os.path.join(top, "packaging", "aur", f), "w").write(
            open(os.path.join(ROOT, "packaging", "aur", f)).read())
    out = os.path.join(base, f"nova-chatmix-{version}.tar.gz")
    with tarfile.open(out, "w:gz") as t:
        t.add(top, arcname=f"nova-chatmix-{version}")
    return open(out, "rb").read()


RELEASE = make_release(NEW)
URL = f"https://github.com/{gui.REPO}/releases/download/v{NEW}/nova-chatmix-{NEW}.tar.gz"
calls = []


def fake_get(url, timeout=10):
    calls.append(url)
    if url == gui.RELEASES_API:
        return json.dumps({"tag_name": f"v{NEW}", "body": "## What's new\n- shiny things",
                           "assets": [{"name": f"nova-chatmix-{NEW}.tar.gz", "browser_download_url": URL}]}).encode()
    if url == URL:
        return RELEASE
    raise OSError("unexpected URL " + url)


gui._http_get = fake_get

# --- versions and the release check
check(gui.version_tuple("1.10.0") > gui.version_tuple("1.9.3") and gui.version_tuple("v2") == (2, 0, 0),
      "versions compare as numbers (1.10 is newer than 1.9)")
info = gui.latest_release(force=True)
check(info == {"version": NEW, "url": URL, "notes": "## What's new\n- shiny things"}, f"latest release found: {info}")
check(gui.is_newer(info) and not gui.is_newer({"version": svc.__version__}) and not gui.is_newer(None),
      "a newer release counts as an update, the same version doesn't")
n = len(calls)
check(gui.latest_release() == info and len(calls) == n, "checked at most every 12 hours (cached)")
gui._http_get = lambda url, timeout=10: json.dumps({"tag_name": "v9.9.10", "assets": [
    {"name": "nova-chatmix-9.9.10.tar.gz", "browser_download_url": "https://evil.example/x.tar.gz"}]}).encode()
check(gui.latest_release(force=True) is None, "only downloads from this repo's own releases")
gui._http_get = fake_get

# --- installed with install.sh (~/.local/bin): files replaced in place
user_bin = os.path.join(tmp, "localbin")
os.makedirs(user_bin)
for f in ("nova-chatmix", "nova-chatmix-gui"):
    open(os.path.join(user_bin, f), "w").write("old")
steps = []
got = gui.run_update(info, steps.append, kind="user", user_bin=user_bin)
new_svc = open(os.path.join(user_bin, "nova-chatmix")).read()
check(got == NEW and f'__version__ = "{NEW}"' in new_svc and "class Window" in
      open(os.path.join(user_bin, "nova-chatmix-gui")).read(), "install.sh copy: both programs replaced")
check(all(os.stat(os.path.join(user_bin, f)).st_mode & stat.S_IXUSR for f in ("nova-chatmix", "nova-chatmix-gui")),
      "...and still executable")
check("systemctl --user try-restart nova-chatmix.service" in log(), "...and the service restarted")
check(steps[0].startswith("Downloading") and steps[-1].startswith("Restarting"), f"progress shown: {steps}")

# --- installed as a pacman package: rebuilt with makepkg, installed with pkexec
open(LOG, "w").close()
steps = []
got = gui.run_update(info, steps.append, kind="package")
lines = log()
check(got == NEW and any(l.startswith("makepkg -f --noconfirm --skipchecksums") for l in lines),
      "package: rebuilt with makepkg from the downloaded release (no second download)")
pk = [l for l in lines if l.startswith("pkexec ")]
check(len(pk) == 1 and pk[0].startswith("pkexec pacman -U --noconfirm ") and
      pk[0].endswith(f"nova-chatmix-{NEW}-1-any.pkg.tar.zst"), f"...installed with pacman through pkexec: {pk}")
check(any("password" in s for s in steps), "...and says a password is coming")
check(not [d for d in os.listdir(tempfile.gettempdir()) if d.startswith("nova-chatmix-update-")],
      "temporary files cleaned up")

os.environ["FAKE_PKEXEC_RC"] = "126"
try:
    gui.run_update(info, kind="package")
    check(False, "closing the password prompt is reported")
except gui.UpdateError as e:
    check("cancelled" in str(e), f"closing the password prompt is reported: {e}")
del os.environ["FAKE_PKEXEC_RC"]

lying = make_release(NEW, claims="1.0.0")
gui._http_get = lambda url, timeout=10: lying
try:
    gui.run_update(info, kind="user", user_bin=user_bin)
    check(False, "a download that isn't the version it claims is refused")
except gui.UpdateError as e:
    check("doesn't contain" in str(e), f"a download that isn't the version it claims is refused: {e}")
gui._http_get = fake_get
try:
    gui.run_update(info, kind="source")
    check(False, "a git checkout isn't overwritten")
except gui.UpdateError:
    check(True, "a git checkout isn't overwritten")

# --- the window: the button appears, does the update, reopens
from PySide6.QtWidgets import QApplication  # noqa: E402

os.remove(gui.UPDATE_CACHE)  # the last check above cached "nothing new"
app = QApplication(sys.argv)
w = gui.Window()
w.show()


def pump(cond, secs=10):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


check(pump(lambda: w.update_btn.isVisible() and w.update_btn.text() == f"Update to {NEW}", 8),
      f"window offers the update by itself: {w.update_btn.text()!r}")
check("shiny things" in w.update_btn.toolTip(), "hovering shows what's new")
ran, relaunched = [], []
gui.install_kind = lambda path=None: "package"


def fake_run(i, progress, kind):
    progress("Building the package…")
    time.sleep(0.2)
    ran.append((i["version"], kind))
    return i["version"]


gui.run_update = fake_run
w.relaunch = lambda: relaunched.append(1)
w.update_btn.click()
check(pump(lambda: relaunched, 8) and ran == [(NEW, "package")], "clicking it installs the update and reopens ChatMix")


def failing(i, progress, kind):
    raise gui.UpdateError("cancelled: the password prompt was closed")


gui.run_update = failing
w.updating = False
w.update_btn.setEnabled(True)
w.update_btn.click()
check(pump(lambda: w.update_btn.text() == "Update failed, try again" and w.update_btn.isEnabled()),
      "a failed update can be retried")
check(w.banner.isVisibleTo(w) and "password prompt" in w.banner_text.text(), "...and says why")
w.close()
check.done()
