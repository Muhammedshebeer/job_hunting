"""Pure Windows task XML builder. Does not load Django settings or credentials."""
import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

NAMESPACE = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
DUBAI = timezone(timedelta(hours=4), name='Asia/Dubai')
TASKS = (('UAEJobAgent0730Discovery', 'run_daily_job_agent', 7, 30, 'PT2H'),
         ('UAEJobAgent0800Report', 'send_daily_job_report', 8, 0, 'PT5M'))


def task_definitions(project_path, profile_id, staff_id, user_sid, *, now=None):
    if type(profile_id) is not int or type(staff_id) is not int or min(profile_id, staff_id) < 1:
        raise ValueError('Existing positive profile/staff IDs are required')
    if not re.fullmatch(r'S-\d+(?:-\d+)+', user_sid):
        raise ValueError('A Windows user SID is required')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('A timezone-aware clock is required')
    root = Path(project_path).resolve()
    python = str(root / '.venv' / 'Scripts' / 'python.exe')
    if any(char in str(root) for char in ('\r', '\n', '\x00')):
        raise ValueError('Invalid project path')
    ET.register_namespace('', NAMESPACE)
    output = {}
    for name, command, hour, minute, deadline in TASKS:
        def node(parent, tag, value=None, **attributes):
            item = ET.SubElement(parent, '{' + NAMESPACE + '}' + tag, attributes)
            if value is not None:
                item.text = str(value)
            return item
        task = ET.Element('{' + NAMESPACE + '}Task', version='1.2')
        info = node(task, 'RegistrationInfo')
        node(info, 'Description', f'{command}: daily {hour:02}:{minute:02} Asia/Dubai; no unattended real submissions')
        trigger = node(node(task, 'Triggers'), 'CalendarTrigger')
        boundary = now.astimezone(DUBAI).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if boundary <= now.astimezone(DUBAI):
            boundary += timedelta(days=1)
        node(trigger, 'StartBoundary', boundary.isoformat())
        node(trigger, 'Enabled', 'true')
        node(node(trigger, 'ScheduleByDay'), 'DaysInterval', 1)
        principal = node(node(task, 'Principals'), 'Principal', id='Author')
        node(principal, 'UserId', user_sid)
        node(principal, 'LogonType', 'InteractiveToken')
        node(principal, 'RunLevel', 'LeastPrivilege')
        settings = node(task, 'Settings')
        for key, value in (
            ('MultipleInstancesPolicy', 'IgnoreNew'), ('DisallowStartIfOnBatteries', 'false'),
            ('StopIfGoingOnBatteries', 'false'), ('AllowHardTerminate', 'true'),
            ('StartWhenAvailable', 'true'), ('RunOnlyIfNetworkAvailable', 'true'),
            ('AllowStartOnDemand', 'true'), ('Enabled', 'true'), ('Hidden', 'false'),
            ('WakeToRun', 'true'), ('ExecutionTimeLimit', deadline), ('Priority', 7)):
            node(settings, key, value)
        restart = node(settings, 'RestartOnFailure')
        node(restart, 'Interval', 'PT5M')
        node(restart, 'Count', 3)
        action = node(node(task, 'Actions', Context='Author'), 'Exec')
        # Only trusted paths and validated integer IDs are encoded; no secrets.
        quoted_python = python.replace("'", "''")
        script = f"$ErrorActionPreference = 'Stop'; try {{ & '{quoted_python}' manage.py {command} --profile-id {profile_id}"
        if command == 'run_daily_job_agent':
            script += f' --staff-id {staff_id}'
        script += "; exit $LASTEXITCODE } catch { [Console]::Error.WriteLine('Task launcher failed; private details suppressed.'); exit 1 }"
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        node(action, 'Command', 'powershell.exe')
        node(action, 'Arguments', '-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand ' + encoded)
        node(action, 'WorkingDirectory', str(root))
        output[name] = ET.tostring(task, encoding='utf-16', xml_declaration=True)
    return output
