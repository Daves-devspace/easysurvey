import json
from django import template

register = template.Library()

@register.filter
# In your model or template tag
def get_service_detail_json(service):
    data = {
        'land_description': service.land_description,
        'service_name': service.service.name,
        'requested_at': service.requested_at.strftime('%b %d, %Y %H:%M'),
        'total_paid': str(service.total_paid()),
        'total_balance': str(service.total_balance()),
        'processes': [
            {
                'name': sp.process.name,
                'cost': str(sp.process.cost),
                'paid': str(sp.paid_amount),
                'pending': str(sp.pending_amount),
                'status': sp.status,
            }
            for sp in service.service_processes.all()
        ],
        'sub_services': [
            {
                'name': cs.sub_service.name,
                'price': str(cs.sub_service.price),
                'paid': str(cs.paid_amount),
                'balance': str(cs.balance),
                'added_on': cs.added_on.strftime('%b %d, %Y'),
            }
            for cs in service.sub_services.all()
        ]
    }
    return json.dumps(data)

