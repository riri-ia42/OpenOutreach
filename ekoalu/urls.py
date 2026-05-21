"""URLs de l app ekoalu."""
from __future__ import annotations

from django.urls import path

from ekoalu import views
from ekoalu.monitoring import views as monitoring_views

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
    path("deals/", views.deals_filtered, name="deals_filtered"),
    path("usage/", views.usage, name="usage"),
    path("recap/", views.daily_recap_today, name="recap_today"),
    path("recap/<str:day>/", views.daily_recap_view, name="recap_day"),
    # Monitoring live
    path("health.json", monitoring_views.health_json, name="health_json"),
    path("live/", monitoring_views.live_dashboard, name="live_dashboard"),
]
