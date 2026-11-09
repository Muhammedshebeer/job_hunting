from django.contrib import admin
from .models import Job
@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display=('company','title','source','posted_at','match_score','accepting_applications')
    list_filter=('source','accepting_applications')
    search_fields=('company','title','description')
