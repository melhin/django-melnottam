"""HTTP views for the e2e test app.

* ``GET /health/``  -> ``{"status": "ok"}`` (used to wait for web workers)
* ``POST /enqueue/`` -> creates a Job row, enqueues the @task, returns its id
* ``GET /status/<id>/`` -> returns the Job row's status (polled by the driver)
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Job
from .tasks import mark_done


@require_http_methods(["GET"])
def health(request):
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_http_methods(["POST"])
def enqueue(request):
    job = Job.objects.create(status=Job.Status.QUEUED)
    mark_done.enqueue(job.id)
    return JsonResponse({"job_id": job.id, "status": job.status})


@require_http_methods(["GET"])
def status(request, job_id: int):
    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"job_id": job.id, "status": job.status})
