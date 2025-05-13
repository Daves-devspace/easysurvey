from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, ExpressionWrapper, DecimalField, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.views.generic import TemplateView

from apps.EasyDocs.models import ClientSubService, SubService, LegalOfficePayout



from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.db.models import Sum

from django.template.loader import render_to_string
from django.http import HttpResponse

class SubServicesStatusView(LoginRequiredMixin, TemplateView):
    template_name = 'Management/subservices_status.html'

    def get_queryset_with_balance(self):
        """
        Returns a queryset of ClientSubService with annotated fields:
        - `annotated_price`: uses overridden_price if provided, otherwise uses default sub_service price.
        - `annotated_balance`: calculated as annotated_price - paid_amount.
        """
        return ClientSubService.objects.select_related(
            'client_service__client', 'sub_service'
        ).annotate(
            annotated_price=Coalesce(F('overridden_price'), F('sub_service__price')),
            annotated_balance=ExpressionWrapper(
                Coalesce(F('overridden_price'), F('sub_service__price')) - F('paid_amount'),
                output_field=DecimalField()
            )
        ).order_by('-added_on')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset_with_balance()

        # Parse optional month filter from GET request (expected format: YYYY-MM)
        month_filter = self.request.GET.get('month_filter')
        if month_filter:
            parsed_date = parse_date(month_filter + "-01")
        else:
            parsed_date = timezone.now().date()
        ctx['month_filter'] = month_filter or ''

        # Filter subservices by selected month
        qs = qs.filter(
            added_on__year=parsed_date.year,
            added_on__month=parsed_date.month
        )

        # All sub-services for the month
        ctx['sub_services'] = qs

        # Sub-services with outstanding balances
        ctx['unpaid_sub_services'] = qs.filter(annotated_balance__gt=0)

        # Legal sub-services that are cleared but not paid to legal office
        ctx['cleared_legal_subservices'] = qs.filter(
            sub_service__department=SubService.RoleChoices.LEGAL,
            annotated_balance__lte=0,
            is_paid_to_legal_office=False
        )

        # All legal sub-services not yet paid to legal office
        ctx['legal_pending_view'] = qs.filter(
            sub_service__department=SubService.RoleChoices.LEGAL,
            is_paid_to_legal_office=False
        )

        # Legal sub-services already paid to legal office
        ctx['legal_paid_history'] = qs.filter(
            sub_service__department=SubService.RoleChoices.LEGAL,
            is_paid_to_legal_office=True
        )

        # Payouts made for this month
        legal_payouts = LegalOfficePayout.objects.filter(
            month__year=parsed_date.year,
            month__month=parsed_date.month
        ).prefetch_related('subservices', 'subservices__client_service__client', 'subservices__sub_service')

        ctx['legal_payouts'] = legal_payouts

        # Add flat list of all subservices in all payouts (merged)
        ctx['payout_subservices'] = ClientSubService.objects.filter(
            legalofficepayouts__in=legal_payouts
        ).select_related('client_service__client', 'sub_service')

        # subservices = []
        # for payout in legal_payouts:
        #     subservices.extend(payout.subservices.all())
        #
        # ctx['payout_subservices'] = subservices

        # Summary metrics
        ctx['total_count'] = legal_payouts.count()
        ctx['total_amount'] = legal_payouts.aggregate(Sum('total_amount'))['total_amount__sum'] or 0

        return ctx

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('HX-Request'):
            # MUST match the include above:
            html = render_to_string(
                'Management/partials/payout_subservices_list.html',
                context, request=self.request
            )
            return HttpResponse(html)
        return super().render_to_response(context, **response_kwargs)






