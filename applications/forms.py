from django import forms
from django.core.exceptions import ValidationError
from pathlib import Path


def validate_confirmation_file(value):
    if value.size > 5 * 1024 * 1024:
        raise ValidationError('Confirmation evidence must be at most 5 MB.')
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff'}
    signature = signatures.get(Path(value.name).suffix.lower())
    header = value.read(8)
    value.seek(0)
    if not signature or not header.startswith(signature):
        raise ValidationError('Upload PDF, PNG, or JPEG confirmation evidence with a matching file type.')
    return value


class ManualSubmissionForm(forms.Form):
    confirmed = forms.BooleanField(label='I personally completed this submission and verified the attached evidence or confirmation reference. Opening the application alone is not a submission.')
    reference = forms.CharField(required=False, max_length=500, label='Confirmation reference')
    evidence = forms.FileField(required=False, validators=[validate_confirmation_file], label='Confirmation evidence (PDF, PNG, JPEG; max 5 MB)')

    def clean(self):
        data = super().clean()
        if not data.get('reference') and not data.get('evidence'):
            raise ValidationError('Provide a confirmation reference or evidence.')
        return data


class PreparationForm(forms.Form):
    extra_questions = forms.CharField(required=False, max_length=10000,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'One employer question per line (optional)'}),
        help_text='Unknown questions are flagged for human clarification. No answers are sent.')


class ReviewForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(required=False,
        label='I reviewed the exact draft, evidence and answers. I explicitly approve opening this verified vacancy for a supervised manual application. This does not submit it.')

    def __init__(self, *args, application, **kwargs):
        super().__init__(*args, **kwargs)
        for key, answer in application.draft_answers.items():
            if answer.get('requires_review'):
                self.fields['resolution_' + key] = forms.CharField(required=False, max_length=2000,
                    label='Human clarification: ' + answer.get('question', key),
                    initial=answer.get('human_resolution', ''), widget=forms.Textarea(attrs={'rows': 3}),
                    help_text='Record a verified answer. If employer clarification is still needed, leave verification unchecked. Do not invent dates or legal authorization.')
                self.fields['verification_' + key] = forms.BooleanField(required=False,
                    initial=answer.get('human_verified', False), label='I independently verified this clarification; it is no longer ambiguous.')

    def resolutions(self):
        return {key.removeprefix('resolution_'): {'answer': value,
                    'verified': self.cleaned_data.get('verification_' + key.removeprefix('resolution_'), False)}
                for key, value in self.cleaned_data.items() if key.startswith('resolution_')}
