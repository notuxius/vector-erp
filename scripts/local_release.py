"""Deploy changed ERP sources to the running Windows demo. Never contacts the VPS."""
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import subprocess as sp
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".artifacts/local-release"
READY = STATE / "ready.json"
DOCKER = ["docker", "--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]
COMPOSE = DOCKER + ["compose", "-p", "vector-erp", "--env-file", ".env", "-f", "compose.yaml"]
BRIDGE = ["-f", "compose.ai-telegram.yaml"]
INPUTS = [".dockerignore", "compose.yaml", "compose.ai-telegram.yaml", "infra",
          "apps/warehouse_core", "services/ai_gateway", "services/telegram_ai_bridge",
          "services/telegram_worker", "services/telegram_bot/content.json"]
ERP = ["backend", "frontend", "websocket", "scheduler", "queue-long", "queue-short"]
IMAGES = ["vector-erp", "vector-erp-ai-gateway", "vector-erp-telegram-ai-bridge"]


def output(args):
    return sp.check_output(args, cwd=ROOT).decode("utf-8").strip()


def sources():
    paths = sp.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z", "--", *INPUTS],
                            cwd=ROOT, creationflags=sp.CREATE_NO_WINDOW)
    return [os.fsdecode(p) for p in sorted(set(paths.split(b"\0")) - {b""}) if (ROOT / os.fsdecode(p)).is_file()]


def fingerprint():
    digest = hashlib.sha256()
    for name in sources():
        digest.update(os.fsencode(name) + b"\0" + hashlib.sha256((ROOT / name).read_bytes()).digest())
    return digest.hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def local_guard():
    status = json.loads(output(COMPOSE + ["exec", "-T", "ai-gateway", "python", "-c",
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/status',timeout=5).read().decode())"]))
    alerts = status["notifications"]
    require(not status["enabled"] and status["allowance_usd"] > 0 and status["cutoff_percent"] == 95
            and status["alert_thresholds"] == alerts["thresholds"] == [75, 95]
            and alerts["enabled"] and alerts["configured"] and not alerts["tests_enabled"]
            and alerts["delivered"] > 0 and not alerts["worker_error"], "Local spending guard failed acceptance")


def deploy(source):
    def run(args):
        sp.run(args, cwd=ROOT, check=True)

    bench = COMPOSE + ["exec", "-T", "backend", "bench", "--site", "frontend"]
    running = output(COMPOSE + ["ps", "--status", "running", "--services"]).splitlines()
    require("backend" in running and "telegram-ai-bridge" not in running and "telegram-bot" not in running,
            "Expected the running local ERP without a live Telegram relay or poller")
    local_guard()
    run([sys.executable, "-m", "unittest", "discover", "-s", "services/ai_gateway"])
    run([sys.executable, "-m", "unittest", "discover", "-s", "services/telegram_ai_bridge"])
    worker = ROOT / "services/telegram_worker"
    env = os.environ.copy()
    env["PATH"] = str(ROOT / ".venv/Scripts") + os.pathsep + env["PATH"]
    sp.run(["node", "--test", *sorted((worker / "test").glob("*.test.mjs"))], cwd=worker, env=env, check=True)
    run(COMPOSE + BRIDGE + ["build", "backend", "ai-gateway", "telegram-ai-bridge"])
    require(fingerprint() == source, "Sources changed during build; test the next build")
    run(bench + ["backup", "--with-files"])
    run(COMPOSE + ["exec", "-T", "ai-gateway", "python", "-c",
        "import sqlite3; sqlite3.connect('file:/data/ledger.sqlite3?mode=ro', uri=True)"
        ".backup(sqlite3.connect('/data/before-local-release.sqlite3'))"])
    run(COMPOSE + ["up", "-d", "--no-build", "--pull", "never"])
    run(COMPOSE + ["wait", "create-site"])
    migration = output(COMPOSE + ["ps", "-aq", "create-site"])
    require(output(DOCKER + ["inspect", "-f", "{{.State.ExitCode}}", migration]) == "0", "Site migration failed")
    require("warehouse_core" in output(bench + ["list-apps"]), "Warehouse Core is missing")
    run(bench + ["execute", "warehouse_core.verify.run"])
    run([sys.executable, "scripts/check_local.py"])
    local_guard()
    images = {name: platform_digest(name + ":0.1.0") for name in IMAGES}
    check_running(images)
    require(fingerprint() == source, "Sources changed during verification; test the next build")
    for name, digest in images.items():
        tag = tested_tag(name, digest)
        run(DOCKER + ["tag", name + ":0.1.0", tag])
        require(platform_digest(tag) == digest, "Image changed while tagging")
    record = {"source": source, "images": images}
    READY.with_suffix(".tmp").write_text(json.dumps(record, indent=2), encoding="utf-8")
    READY.with_suffix(".tmp").replace(READY)
    print("Ready for local testing:", release_id(record), flush=True)


def platform_digest(tag):
    # Build attestations can change the index ID without changing the running Linux image.
    return output(DOCKER + ["image", "inspect", "--platform", "linux/amd64", "-f", "{{.Id}}", tag])


def check_running(images):
    services = ERP + ["ai-gateway"]
    rows = output(DOCKER + ["inspect", "-f", "{{.ImageManifestDescriptor.Digest}} {{.State.Running}}",
                            *(f"vector-erp-{service}-1" for service in services)]).splitlines()
    expected = [images[IMAGES[0]] + " true"] * len(ERP) + [images[IMAGES[1]] + " true"]
    require(rows == expected, "Running containers do not match the tested local release")


def tested_tag(name, digest):
    return name + ":tested-" + digest.split(":")[1][:16]


def release_id(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]


def pack(approved):
    record = json.loads(READY.read_text())
    require(approved == release_id(record) and record["source"] == fingerprint(),
            "This is not the approved local release")
    check_running(record["images"])
    tags = []
    for name, digest in record["images"].items():
        tag = tested_tag(name, digest)
        require(platform_digest(tag) == digest, "Tested image changed")
        tags.append(tag)
    folder = STATE / approved
    if not folder.exists():
        with tempfile.TemporaryDirectory(dir=STATE) as temp:
            bundle = Path(temp) / approved
            bundle.mkdir()
            with tarfile.open(bundle / "source.tar.gz", "w:gz") as archive:
                for name in sources():
                    archive.add(ROOT / name, arcname=name, recursive=False)
            sp.run(DOCKER + ["image", "save", "--platform", "linux/amd64", "-o", str(bundle / "images.tar"), *tags], check=True)
            require(record["source"] == fingerprint() and record == json.loads(READY.read_text()), "Release changed during export")
            (bundle / "release.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            bundle.rename(folder)
    require(json.loads((folder / "release.json").read_text()) == record, "Unexpected existing release bundle")
    print("Local promotion bundle:", folder)


def once():
    with (STATE / "deploy.lock").open("a+b") as lock:
        lock.seek(0)
        deadline = time.monotonic() + 900
        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except PermissionError:
                require(time.monotonic() < deadline, "Another local deployment is still running")
                time.sleep(2)
        source = fingerprint()
        if READY.exists():
            record = json.loads(READY.read_text())
            if record["source"] == source:
                try:
                    check_running(record["images"])
                except (RuntimeError, sp.CalledProcessError):
                    pass  # Reconcile a local stack that was replaced outside the watcher.
                else:
                    print("Already deployed:", release_id(record))
                    return
        READY.unlink(missing_ok=True)
        deploy(source)


def watch():
    previous = failed = None
    while True:
        try:
            source = fingerprint()
            ready = json.loads(READY.read_text())["source"] if READY.exists() else None
            if source == previous and source not in (ready, failed):
                # A stopped Docker Desktop or sandbox stays stopped; retry after it starts.
                live = sp.run(COMPOSE + ["ps", "-q", "backend"], cwd=ROOT, capture_output=True,
                              creationflags=sp.CREATE_NO_WINDOW)
                if live.returncode == 0 and live.stdout.strip():
                    with (STATE / "deploy.log").open("wb") as log:
                        result = sp.run([sys.executable.replace("pythonw.exe", "python.exe"), "-u", __file__],
                                        cwd=ROOT, stdout=log, stderr=sp.STDOUT,
                                        creationflags=sp.CREATE_NO_WINDOW)
                    failed = source if result.returncode else None
            if previous != source:
                failed = None
            previous = source
        except (OSError, ValueError, sp.SubprocessError) as error:
            (STATE / "watch-error.txt").write_text(str(error), encoding="utf-8")
        time.sleep(20)


if __name__ == "__main__":
    os.chdir(ROOT)
    STATE.mkdir(parents=True, exist_ok=True)
    if sys.argv[1:] == ["--watch"]:
        with (STATE / "watch.lock").open("a+b") as lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SystemExit("Local watcher is already running")
            watch()
    elif sys.argv[1:] == ["--candidate"]:
        if not READY.exists():
            raise SystemExit("No verified local release yet. Check .artifacts/local-release/deploy.log.")
        record = json.loads(READY.read_text())
        require(record["source"] == fingerprint(), "Local changes have not passed verification")
        check_running(record["images"])
        print(release_id(record))
    elif len(sys.argv) == 3 and sys.argv[1] == "--pack":
        pack(sys.argv[2])
    elif not sys.argv[1:]:
        once()
    else:
        raise SystemExit("usage: local_release.py [--watch | --candidate | --pack RELEASE_ID]")
