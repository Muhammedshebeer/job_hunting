from django.contrib import admin
from .models import Job, DiscoveryRun, RejectedVacancy
@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display=('company','title','source','posted_at','match_score','accepting_applications','requires_review')
    list_filter=('source','accepting_applications','requires_review')
    readonly_fields=('review_reasons','discovery_metadata')
    search_fields=('company','title','description')


class ReadOnlyDiscoveryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DiscoveryRun)
class DiscoveryRunAdmin(ReadOnlyDiscoveryAdmin):
    list_display = ('id', 'candidate', 'status', 'queued_at', 'started_at', 'finished_at')
    list_filter = ('status',)


@admin.register(RejectedVacancy)
class RejectedVacancyAdmin(ReadOnlyDiscoveryAdmin):
    list_display = ('run', 'company', 'title', 'location', 'source', 'posted_at', 'rejection_code')
    list_filter = ('source', 'rejection_code')
