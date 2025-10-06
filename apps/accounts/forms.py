# apps/accounts/forms.py
from django import forms
from django.utils import timezone
from decimal import Decimal
from apps.accounts.models import CashbookEntry

class InstitutionPayoutForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        label="Amount",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter payout amount",
                "step": "0.01",
                "min": "0"
            }
        ),
    )

    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Institution payout description",
            }
        ),
    )

    payout_date = forms.DateField(
        required=False,
        label="Date",
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}
        ),
    )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        current_balance = CashbookEntry.current_balance()

        if amount > current_balance:
            raise forms.ValidationError(
                f"Insufficient funds. Current balance is {current_balance}, but you requested {amount}."
            )
        return amount




class OpeningBalanceForm(forms.ModelForm):
    class Meta:
        model = CashbookEntry
        fields = ["date", "amount", "description"]

    date = forms.DateField(
        initial=timezone.now().date,
        widget=forms.DateInput(attrs={"class": "form-control", "readonly": "readonly"}),
        required=True
    )

    amount = forms.DecimalField(
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        required=True
    )

    description = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        required=False
    )