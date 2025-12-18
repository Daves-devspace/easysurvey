from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from django.contrib import messages 
from apps.tenant_management.models import Property, Tenant
from apps.tenant_management.forms import AnnouncementForm
from apps.tenant_management.comm import sms_utils
from django.http import HttpResponse


class AnnouncementPreviewView(View):
    def post(self, request, pk):
        property_obj = get_object_or_404(Property, pk=pk)
        form = AnnouncementForm(request.POST)
        
        if form.is_valid():
            message = form.cleaned_data['message']
            # Calculate Recipients: Active tenants in this property
            active_tenants = Tenant.objects.filter(property=property_obj, leases__is_active=True).distinct()
            recipient_count = active_tenants.count()
            
            # Simple cost estimation (approx 1 KES per SMS)
            estimated_cost = recipient_count * 1.0 
            
            context = {
                'property': property_obj,
                'message': message,
                'recipient_count': recipient_count,
                'estimated_cost': estimated_cost,
            }
            return render(request, 'properties/partials/comm_preview.html', context)
        
        return HttpResponse("Invalid Form", status=400)

class AnnouncementSendView(View):
    def post(self, request, pk):
        property_obj = get_object_or_404(Property, pk=pk)
        message = request.POST.get('message')
        
        if not message:
            messages.error(request, "Message cannot be empty.")
            return redirect('property_detail', pk=pk)

        # Send via Utils
        count = sms_utils.send_property_announcement(property_obj, message)
        
        if count > 0:
            messages.success(request, f"Announcement sent to {count} tenants.")
        else:
            messages.warning(request, "No active tenants found or failed to send.")
            
        return redirect('property_detail', pk=pk)