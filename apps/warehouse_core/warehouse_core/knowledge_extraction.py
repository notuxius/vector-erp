"""Run untrusted document parsing outside the web worker, with no inherited credentials."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_TEXT = 100_000
STATUSES = {"Extracted", "Partial", "No Text", "Encrypted", "Limit Exceeded", "Failed"}


def extract(raw, suffix):
    if suffix not in (".pdf", ".docx", ".xlsx", ".txt"):
        return {"status": "Not Applicable", "text": "", "message": "Автоматичне витягування доступне для PDF, DOCX, XLSX та TXT."}
    fallback = {"status": "Failed", "text": "", "message": "Обробку документа зупинено. Файл збережено; спробуйте менший або спрощений документ."}
    if len(raw) > 5 * 1024 * 1024 or sys.platform != "linux":
        return fallback
    try:
        with tempfile.TemporaryDirectory(prefix="knowledge-extract-") as cwd:
            result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name("knowledge_extraction_worker.py")), suffix],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=False,
                cwd=cwd, env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        if result.returncode or len(result.stdout) > 650_000:
            return fallback
        value = json.loads(result.stdout)
        if (not isinstance(value, dict) or value.get("status") not in STATUSES or not isinstance(value.get("text"), str)
                or len(value["text"]) > MAX_TEXT or not isinstance(value.get("message"), str)
                or len(value["message"]) > 4000):
            return fallback
        if value["status"] not in ("Extracted", "Partial"):
            value["text"] = ""
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return fallback
