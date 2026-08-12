from django.db import models


class Job(models.Model):
    """A single job row written by the @task below.

    The test driver enqueues a task, then polls this table to confirm the
    task worker has picked it up and completed it.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        DONE = "done", "Done"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Job<{self.pk} {self.status}>"
