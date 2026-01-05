from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
import json
from .models import FirebaseConfig, FCMToken, Notification, PendingPushNotification

class FirebaseConfigForm(forms.ModelForm):
    """
    Custom form to allow uploading the JSON file instead of pasting text.
    The file is read in memory and saved to the encrypted text field.
    """
    service_account_file = forms.FileField(
        required=False, 
        help_text="<strong>Recommended:</strong> Upload your <code>firebase-service-account.json</code> file here. It will be automatically read, encrypted, and stored."
    )

    class Meta:
        model = FirebaseConfig
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        json_file = cleaned_data.get('service_account_file')
        json_text = cleaned_data.get('service_account_json')

        # Logic: If a file is uploaded, it takes precedence over the text box.
        if json_file:
            try:
                # 1. Read the file content
                file_content = json_file.read().decode('utf-8')
                
                # 2. Validate it is real JSON
                json.loads(file_content)
                
                # 3. Inject it into the model field (which will be encrypted on save)
                cleaned_data['service_account_json'] = file_content
                
            except json.JSONDecodeError:
                raise ValidationError("The uploaded file is not valid JSON.")
            except UnicodeDecodeError:
                raise ValidationError("The uploaded file must be a UTF-8 text file.")
        
        # Validation: If creating a NEW config, one of them is required.
        elif not json_text and not self.instance.pk:
            raise ValidationError("You must either upload a JSON file or paste the credentials.")

        return cleaned_data

@admin.register(FirebaseConfig)
class FirebaseConfigAdmin(admin.ModelAdmin):
    form = FirebaseConfigForm
    list_display = ('project_id', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('project_id', 'messaging_sender_id')
    
    fieldsets = (
        ('Frontend Settings (Public)', {
            'fields': (
                'api_key', 
                'auth_domain', 
                'project_id', 
                'storage_bucket', 
                'messaging_sender_id', 
                'app_id', 
                'vapid_key'
            ),
            'description': "Get these from Firebase Console > Project Settings > General > Your Apps."
        }),
        ('Backend Credentials (Encrypted)', {
            'fields': ('service_account_file', 'service_account_json'),
            'description': (
                "Upload your JSON file <strong>OR</strong> paste the content below. <br/>"
                "The system will encrypt this data immediately upon saving."
            )
        }),
        ('Status', {
            'fields': ('is_active',),
        }),
    )

@admin.register(FCMToken)
class FCMTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'token_snippet', 'is_active', 'updated_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')

    def token_snippet(self, obj):
        return f"{obj.token[:30]}..." if obj.token else "-"

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'is_read', 'seen_by_admin', 'created_at')
    list_filter = ('is_read', 'seen_by_admin', 'created_at')
    search_fields = ('user__username', 'title')
    actions = ['mark_as_read']

    @admin.action(description='Mark selected notifications as read')
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)

@admin.register(PendingPushNotification)
class PendingPushNotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'sent', 'sent_at', 'created_at')
    list_filter = ('sent', 'created_at')