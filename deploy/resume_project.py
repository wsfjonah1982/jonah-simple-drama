"""Resume a generation whose background thread was killed by a service restart (run on the server).

    cd /opt/drama-app && ./venv/bin/python /path/to/resume_project.py <project id>

The pipeline runs in a thread of the gunicorn worker, so `systemctl restart drama-app` kills any project
that is in step 1 (status `image_generating`) or between the steps (`submitting`). Projects that are
`queued` or `running` already exist as Seedance tasks at BytePlus and are unaffected.

* `submitting` with a saved character sheet and no task id: only the Seedance submit is redone (no new
  Seedream charge). Before running this, check that BytePlus did not already create a task for it
  (list recent tasks; a task id encodes its creation time), otherwise you pay for a duplicate video.
* `image_generating`: Seedream is called again, because the interrupted call's result is lost.
"""
import sys

sys.path.insert(0, ".")
import app
import project_store
import seedance

if len(sys.argv) != 2:
    sys.exit(__doc__)
pid = sys.argv[1]
job = project_store.load(pid)
if not job:
    sys.exit(f"no such project: {pid}")
if job["task_id"] or job["status"] not in ("image_generating", "submitting"):
    sys.exit(f"nothing to resume: status={job['status']} task_id={job['task_id']}")

reason = ("service restarted mid-run; continuing from the saved character sheet (Seedream not re-run)"
          if job["status"] == "submitting" and job["character"]
          else "service restarted mid-run; starting again from step 1")
project_store.log(pid, "resumed", reason=reason)
try:
    if job["status"] == "image_generating":
        app._image_stage(job)
    if job["status"] == "submitting":
        app._video_stage(job)
except Exception as exc:
    app._fail(job, "internal", "Something went wrong on the server. Please try again.", seedance.describe_error(exc))
job = project_store.load(pid)
print(f"{pid}: status={job['status']} task_id={job['task_id']}")
