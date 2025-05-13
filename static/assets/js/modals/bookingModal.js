
document.addEventListener('DOMContentLoaded', () => {
  // 1️⃣ Activate Bootstrap tooltips for all message previews
  document.querySelectorAll('.preview-with-tooltip').forEach(el => {
    new bootstrap.Tooltip(el);
  });

  // 2️⃣ Wire up each booking form
  document.querySelectorAll('.edit-booking-form').forEach(form => {
    const bookingId       = form.dataset.id;
    const clientName      = form.dataset.clientName;
    const svcName         = form.dataset.serviceName;
    const dateInput       = form.querySelector('.scheduled-date-input');
    const messageTextarea = form.querySelector('.message-input');
    const previewSpan     = document.getElementById(`msg-preview-${bookingId}`);

    // grab or create the tooltip instance so we can update it later
    let tooltipInstance = previewSpan
      ? bootstrap.Tooltip.getOrCreateInstance(previewSpan)
      : null;

    // 🔄 When the user changes the date in the modal
    dateInput.addEventListener('input', () => {
      const dt     = new Date(dateInput.value);
      const when   = dt.toLocaleString('en-KE', {
        weekday: 'long',
        year:     'numeric',
        month:    'long',
        day:      'numeric',
        hour:     '2-digit',
        minute:   '2-digit'
      });
      const fullMsg  = `Hi ${clientName}, surveyors for ${svcName} have been scheduled for ${when}.`;
      const shortMsg = fullMsg.slice(0, 60) + (fullMsg.length > 60 ? '…' : '');

      // Update modal textarea
      messageTextarea.value = fullMsg;

      if (previewSpan) {
        // Update table preview text
        previewSpan.textContent = shortMsg;
        // Update tooltip content
        previewSpan.setAttribute('title', fullMsg);
        // Re-create tooltip so it picks up the new title
        tooltipInstance.dispose();
        tooltipInstance = new bootstrap.Tooltip(previewSpan);
      }
    });

    // 🚀 AJAX‐submit the form
    form.addEventListener('submit', e => {
      e.preventDefault();
      const url      = form.action;
      const formData = new FormData(form);

      fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': formData.get('csrfmiddlewaretoken') },
        body: formData
      })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          // 1) update the truncated preview
          if (previewSpan) {
            const short = data.dispatch_message.slice(0, 60)
                        + (data.dispatch_message.length > 60 ? '…' : '');
            previewSpan.textContent = short;
            previewSpan.setAttribute('title', data.dispatch_message);
            tooltipInstance.dispose();
            tooltipInstance = new bootstrap.Tooltip(previewSpan);
          }
          // 2) update the scheduled-date cell (2nd <td>)
          const row = document.getElementById(`booking-row-${bookingId}`);
          if (row) row.cells[1].textContent = data.scheduled_date;
          // 3) hide the modal
          bootstrap.Modal.getInstance(
            document.getElementById(`editBookingModal${bookingId}`)
          ).hide();
        } else {
          alert('Update failed. Please try again.');
        }
      })
      .catch(() => alert('Error saving booking.'));
    });
  });
});

