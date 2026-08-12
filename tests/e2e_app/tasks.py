"""A trivial @task that the task worker executes.

It writes a row to the DB so the test driver can observe completion without
needing any IPC channel back to the parent process.
"""

from django.tasks import task

from .models import Job


@task
def mark_done(job_id: int) -> None:
    """Flip the given Job row's status to 'done'."""
    Job.objects.filter(pk=job_id).update(status=Job.Status.DONE)
