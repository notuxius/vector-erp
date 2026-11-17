"""Read Contabo's private ERP feedback queue, or attach a finding from a UTF-8 text file."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    actions = cli.add_subparsers(dest="action", required=True)
    actions.add_parser("pending")
    save = actions.add_parser("save")
    save.add_argument("report")
    save.add_argument("finding", type=Path)
    args = cli.parse_args()
    prefix = "warehouse_core.warehouse_core.doctype.vectoria_feedback.vectoria_feedback."
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "vector-erp-vps6",
               "docker", "exec", "-i", "vector-erp-backend-1", "bench", "--site", "frontend", "execute"]
    payload = b""
    if args.action == "pending":
        command.append(prefix + "pending")
    else:
        finding = args.finding.read_text(encoding="utf-8-sig")
        # SSH commands and bench --args interpret code; all client-derived data travels on stdin.
        command.append(prefix + "save_finding")
        payload = json.dumps({"name": args.report, "analysis": finding}).encode()
    subprocess.run(command, input=payload, check=True)


if __name__ == "__main__":
    main()
