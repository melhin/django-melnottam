from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("enqueue/", views.enqueue, name="enqueue"),
    path("status/<int:job_id>/", views.status, name="status"),
]
