"""URLs de l app ekoalu."""
from __future__ import annotations

from django.urls import path

from ekoalu import views

app_name = "ekoalu"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("messages/", views.outbound_list, name="outbound_list"),
    path("messages/<int:pk>/", views.outbound_detail, name="outbound_detail"),
    path("campaigns/", views.campaigns_list, name="campaigns_list"),
    path("campaigns/<int:pk>/", views.campaign_detail, name="campaign_detail"),
    path("leads/add/", views.leads_add, name="leads_add"),
    path("leads/<str:slug>/", views.lead_detail, name="lead_detail"),
    path("companies/", views.companies_list, name="companies_list"),
    path("inbox/", views.inbox, name="inbox"),
    path("companies-validation/", views.companies_validation, name="companies_validation"),
]
