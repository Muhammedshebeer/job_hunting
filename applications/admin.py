from django.contrib import admin
from .models import Application
from .models import DailyReport, ApplicationEvent, DailyApplicationBudget, ApplicationSlot
@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display=('candidate','job','status','attempted_at','submitted_at','confirmation_reference')
    list_filter=('status',)
    search_fields=('job__company','job__title','confirmation_reference')
    readonly_fields=('status','draft_answers','cover_letter','preparation_snapshot','prepared_at','updated_at',
                     'approved_at','approved_by','approval_digest','submitted_at','confirmation_reference','confirmation_evidence',
                     'externally_opened_at','externally_opened_by','submission_recorded_at','submission_recorded_by')


class ReadOnlyWorkflowAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


admin.site.register(DailyReport, ReadOnlyWorkflowAdmin)
admin.site.register(ApplicationEvent, ReadOnlyWorkflowAdmin)
admin.site.register(DailyApplicationBudget, ReadOnlyWorkflowAdmin)
admin.site.register(ApplicationSlot, ReadOnlyWorkflowAdmin)
