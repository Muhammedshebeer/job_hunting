from django.contrib import admin
from .models import Application
@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display=('candidate','job','status','attempted_at','submitted_at','confirmation_reference')
    list_filter=('status',)
    search_fields=('job__company','job__title','confirmation_reference')
