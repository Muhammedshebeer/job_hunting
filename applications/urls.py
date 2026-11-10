from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('discovery/run/', views.queue_discovery, name='queue_discovery'),
    path('reports/send/', views.queue_daily_report, name='queue_daily_report'),
    path('discovery/runs/<int:pk>/', views.discovery_run_detail, name='discovery_run_detail'),
    path('discovery/runs/<int:pk>/status/', views.discovery_run_status, name='discovery_run_status'),
    path('jobs/<int:pk>/', views.job_detail, name='job_detail'),
    path('jobs/<int:pk>/prepare/', views.prepare, name='prepare_application'),
    path('drafts/<int:pk>/', views.application_detail, name='application_detail'),
    path('drafts/<int:pk>/open/', views.open_approved_application, name='open_application'),
    path('drafts/<int:pk>/record-submission/', views.record_submission, name='record_submission'),
    path('drafts/<int:pk>/evidence/', views.evidence_download, name='application_evidence'),
]
