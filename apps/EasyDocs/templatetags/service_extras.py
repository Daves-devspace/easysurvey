import json
from django import template

register = template.Library()

@register.filter
def get_service_detail_json(service):
    return json.dumps({
        'land_description': service.land_description,
        'service_name': service.service.name,
        'requested_at': service.requested_at.strftime("%b %d, %Y %H:%M"),
        'total_paid': str(service.total_paid()),
        'total_balance': str(service.total_balance()),
        'processes': [
            {
                'name': p.process.name,
                'cost': str(p.process.cost),
                'paid': str(p.paid_amount),
                'pending': str(p.pending_amount),
                'status': p.status
            }
            for p in service.service_processes.all()
        ]
    })
