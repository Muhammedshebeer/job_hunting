"""Write reviewable task definitions only. Never registers tasks or reads .env."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from applications.services.task_schedule import task_definitions

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--profile-id', type=int, default=1)
parser.add_argument('--staff-id', type=int, required=True)
parser.add_argument('--user-sid', required=True)
args = parser.parse_args()
directory = ROOT / 'output' / 'scheduled_tasks'
definitions = task_definitions(ROOT, args.profile_id, args.staff_id, args.user_sid)
directory.mkdir(parents=True, exist_ok=True)
for name, xml in definitions.items():
    (directory / (name + '.xml')).write_bytes(xml)
    print(f'Prepared {name}: explicit Asia/Dubai UTC+04:00 daily trigger.')
