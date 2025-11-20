document.addEventListener('DOMContentLoaded', function () {
  var paymentModalEl = document.getElementById('paymentModal');

  if (!paymentModalEl) return;

  paymentModalEl.addEventListener('show.bs.modal', function (event) {
    var button = event.relatedTarget;
    var invoiceId = button ? button.getAttribute('data-invoice-id') : null;
    var invoiceBalance = button ? button.getAttribute('data-invoice-balance') : null;
    var paymentUrl = button ? button.getAttribute('data-payment-url') : null;

    var invoiceInput = document.getElementById('id_invoice_id');
    if (invoiceInput) invoiceInput.value = invoiceId || '';

    var amountInput = document.getElementById('id_amount');
    if (invoiceBalance && amountInput && (!amountInput.value || amountInput.value === '')) {
      amountInput.value = invoiceBalance;
    }

    var form = document.getElementById('tenantPaymentForm');
    if (paymentUrl && form) {
      form.action = paymentUrl;
    }
  });

  // simple client-side guard
  var form = document.getElementById('tenantPaymentForm');
  if (form) {
    form.addEventListener('submit', function (e) {
      var amount = parseFloat(document.getElementById('id_amount').value);
      if (!amount || amount <= 0) {
        e.preventDefault();
        alert('Please enter a valid amount > 0');
      }
    });
  }
});
