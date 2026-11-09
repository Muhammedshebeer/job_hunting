from django.contrib import admin
from .models import CandidateProfile
@admin.register(CandidateProfile)
class CandidateProfileAdmin(admin.ModelAdmin):
    list_display=('name','notice_days','active')
