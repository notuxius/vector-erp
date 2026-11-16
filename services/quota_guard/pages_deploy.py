"""Deploy an MTech Pages project only below the conservative 95% monthly budget.

Uses the existing local Wrangler login; never uploads that OAuth token to a Worker.
Run from the repository root: python -m services.quota_guard.pages_deploy PROJECT
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

from services.telegram_worker.cloud_ops import cf

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = {
    'mtech-design-preview': ROOT / 'sites/mtech-design-refresh/dist',
}


def monthly_deployments(api=cf, now=None):
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = 0
    for project_page in range(1, 101):
        projects = api(f'pages/projects?per_page=10&page={project_page}')
        for project in projects:
            for page in range(1, 101):
                rows = api(f'pages/projects/{project["name"]}/deployments?per_page=25&page={page}')
                dates = [datetime.fromisoformat(row['created_on'].replace('Z', '+00:00')) for row in rows]
                count += sum(date >= start for date in dates)
                if len(rows) < 25 or any(date < start for date in dates):
                    break
            else:
                raise RuntimeError('Deployment history truncated; deployment blocked')
        if len(projects) < 10:
            return count
    raise RuntimeError('Project inventory truncated; deployment blocked')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', choices=PROJECTS)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    # Serialize this wrapper locally to prevent two local deploys spending the last slot.
    lock = ROOT / '.artifacts/pages-deploy.lock'
    lock.parent.mkdir(exist_ok=True)
    owned = False
    try:
        with lock.open('x'):
            owned = True
            count = monthly_deployments()
            print(f'Account Pages deployments this UTC month: {count}/500; stop at 475.')
            if count >= 475:
                raise RuntimeError('95% Pages deployment budget reached; deployment blocked')
            if args.check_only:
                return
            runner = shutil.which('npx.cmd') or shutil.which('npx')
            if not runner:
                raise RuntimeError('npx is not available')
            subprocess.run([runner, 'wrangler', 'pages', 'deploy', str(PROJECTS[args.project]),
                '--project-name', args.project, '--branch', 'main'], cwd=ROOT, check=True)
    except FileExistsError:
        raise RuntimeError('Another guarded deploy is running; inspect .artifacts/pages-deploy.lock if stale') from None
    finally:
        if owned:
            lock.unlink()


if __name__ == '__main__':
    main()
