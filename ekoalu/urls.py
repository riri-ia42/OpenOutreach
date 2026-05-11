"""URLs de l app ekoalu."""
from __future__ import annotations

from django.urls import path

from ekoalu import views

app_name = "ekoalu"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]
