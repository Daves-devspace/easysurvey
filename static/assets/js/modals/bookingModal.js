// bookingmodals.js (unified create/edit modal + shared auto-message logic)
document.addEventListener('DOMContentLoaded', () => {
  const DEBUG = false; // set true while debugging

  // Build full + short dispatch messages
  function buildDispatchMessage(clientName, svcName, dateValue, isGround=false) {
    if (!dateValue) return { full: '', short: '' };

    const dt = new Date(dateValue);
    const when = dt.toLocaleString('en-KE', {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });

    let fullMsg;
    if (isGround) {
      fullMsg = `Hello ${clientName}, surveyors assigned for your "${svcName}" service have been scheduled on ${when} for ground/site work.`;
    } else {
      fullMsg = `Hello ${clientName}, surveyors assigned for your "${svcName}" service have been scheduled on ${when}.`;
    }

    const shortMsg = fullMsg.length > 60 ? fullMsg.slice(0, 60) + '…' : fullMsg;
    return { full: fullMsg, short: shortMsg };
  }

  // Tooltip helper
  function initTooltip(el) {
    if (!el) return;
    const inst = bootstrap.Tooltip.getInstance(el);
    if (inst) inst.dispose();
    return new bootstrap.Tooltip(el);
  }

  // Resolve name fallbacks
  function resolveName(primary, serviceId, type) {
    if (primary && primary.trim()) return primary.trim();

    if (serviceId) {
      const opener = document.querySelector(`.open-booking-modal[data-service-id="${serviceId}"]`);
      if (opener?.dataset) {
        if (type === 'service' && opener.dataset.serviceName) return opener.dataset.serviceName.trim();
        if (type === 'client' && opener.dataset.clientName) return opener.dataset.clientName.trim();
      }
    }
    return '';
  }

  // Insert new booking row
  function insertBookingRow(table, booking) {
    const tbody = table.tBodies[0] || table;
    tbody.querySelector('.no-bookings-row')?.remove();

    const tr = document.createElement('tr');
    tr.id = `booking-row-${booking.id}`;
    tr.innerHTML = `
      <td>${booking.id}</td>
      <td>${booking.scheduled_date}</td>
      <td>
        <span id="msg-preview-${booking.id}" class="preview-with-tooltip"
              data-bs-toggle="tooltip" title="${booking.dispatch_message || ''}">
          ${(booking.dispatch_message||'').slice(0,60)}${(booking.dispatch_message?.length>60)?'…':''}
        </span>
      </td>
    `;
    tbody.appendChild(tr);
    initTooltip(document.getElementById(`msg-preview-${booking.id}`));
  }

  // Update existing booking row
  function updateBookingRow(bookingId, scheduled_date, dispatch_message) {
    const row = document.getElementById(`booking-row-${bookingId}`);
    if (!row) return false;

    if (row.cells[1]) row.cells[1].textContent = scheduled_date;

    const previewSpan = document.getElementById(`msg-preview-${bookingId}`);
    if (previewSpan) {
      const short = dispatch_message.length > 60 ? dispatch_message.slice(0, 60) + '…' : dispatch_message;
      previewSpan.textContent = short;
      previewSpan.setAttribute('title', dispatch_message);
      initTooltip(previewSpan);
    }
    return true;
  }

  // Initialize booking form (modal or inline)
  function initBookingForm(form) {
    if (!form) return;

    let clientName = form.dataset.clientName || '';
    let svcName = form.dataset.serviceName || '';
    const serviceId = form.dataset.serviceId || null;
    const bookingId = form.dataset.bookingId || null;
    const isGround = form.dataset.isGround === 'true';

    const dateInput = form.querySelector('.scheduled-date-input');
    const msgInput = form.querySelector('.message-input');
    const previewSpan = bookingId ? document.getElementById(`msg-preview-${bookingId}`) : null;

    // clientName = resolveName(clientName, serviceId, 'client');
    // svcName = resolveName(svcName, serviceId, 'service');

    if (DEBUG) console.log('initBookingForm resolved', { clientName, svcName, isGround, serviceId, bookingId });

    if (msgInput) {
      msgInput.addEventListener('input', () => msgInput.dataset.userEdited = 'true');
    }

    if (dateInput) {
     dateInput.addEventListener('input', () => {
      const realSvcName = resolveName(form.dataset.serviceName, form.dataset.serviceId, 'service');
      const realClientName = resolveName(form.dataset.clientName, form.dataset.serviceId, 'client');
      const isGround = form.dataset.isGround === 'true';
      const { full, short } = buildDispatchMessage(realClientName, realSvcName, dateInput.value, isGround);


        if (msgInput && !msgInput.dataset.userEdited) msgInput.value = full;
        if (previewSpan) {
          previewSpan.textContent = short;
          previewSpan.setAttribute('title', full);
          initTooltip(previewSpan);
        }
        if (DEBUG) console.log('prefill ->', { realClientName, realSvcName, full });
      });
    }

    // Handle submit
    form.addEventListener('submit', e => {
      e.preventDefault();
      const fd = new FormData(form);

      fetch(form.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': fd.get('csrfmiddlewaretoken') },
        body: fd
      })
      .then(r => r.json())
      .then(data => {
        if (!data) return alert('Empty response.');

        if (data.success) {
          const isEdit = !!form.dataset.bookingId;
          if (isEdit) {
            updateBookingRow(form.dataset.bookingId, data.scheduled_date, data.dispatch_message || '');
          } else {
            const table = document.getElementById(`bookings-table-${serviceId}`);
            if (table) insertBookingRow(table, {
              id: data.id,
              scheduled_date: data.scheduled_date,
              dispatch_message: data.dispatch_message || ''
            });
          }

          // close modal if inside one
          const modalEl = form.closest('.modal');
          if (modalEl) bootstrap.Modal.getInstance(modalEl)?.hide();

          if (!isEdit) {
            form.reset();
            delete msgInput.dataset.userEdited;
            delete form.dataset.bookingId;
          }
        } else {
          alert(data.error || JSON.stringify(data.errors) || 'Save failed.');
        }
      })
      .catch(err => { console.error(err); alert('Error saving booking.'); });
    });
  }

  // Setup unified booking modal
  (function setupModalOpener(){
    const modalEl = document.getElementById('bookingModal');
    if (!modalEl) return;

    const modalForm = modalEl.querySelector('form.booking-form');
    if (!modalForm) return;

    initBookingForm(modalForm);

    const bsModal = new bootstrap.Modal(modalEl);
    const schedInput = modalForm.querySelector('.scheduled-date-input');
    const msgInput = modalForm.querySelector('.message-input');
    const clientLabel = document.getElementById('bookingModalClient');
    const serviceLabel = document.getElementById('bookingModalService');

    document.querySelectorAll('.open-booking-modal').forEach(btn => {
      btn.addEventListener('click', () => {
        console.log("Raw button dataset:", btn.dataset);
        console.log("clientName from dataset:", btn.dataset.clientName);
        const { action, url, clientName, serviceName, serviceId, bookingId, scheduled, message, isGround } = btn.dataset;

        modalForm.action = url;
        modalForm.dataset.clientName = resolveName(clientName, serviceId, 'client');
        modalForm.dataset.serviceName = resolveName(serviceName, serviceId, 'service');
        modalForm.dataset.serviceId = serviceId;
        modalForm.dataset.isGround = isGround === 'true';

        if (action === 'edit' && bookingId) {
          modalForm.dataset.bookingId = bookingId;
          schedInput.value = scheduled || '';
          msgInput.value = message || '';
          if (message) msgInput.dataset.userEdited = 'true';
        } else {
          delete modalForm.dataset.bookingId;
          schedInput.value = '';
          msgInput.value = '';
          delete msgInput.dataset.userEdited;
        }

        if (clientLabel) clientLabel.textContent = modalForm.dataset.clientName ? `Client: ${modalForm.dataset.clientName}` : '';
        if (serviceLabel) serviceLabel.textContent = modalForm.dataset.serviceName ? `Service: ${modalForm.dataset.serviceName}` : '';

        if (DEBUG) console.log('modal opened', {
          client: modalForm.dataset.clientName,
          service: modalForm.dataset.serviceName,
          isGround: modalForm.dataset.isGround,
          serviceId
        });

        bsModal.show();
      });
    });
  })();

  // Init inline forms + tooltips
  document.querySelectorAll('.booking-form:not(#bookingModal form.booking-form)').forEach(f => initBookingForm(f));
  document.querySelectorAll('.preview-with-tooltip').forEach(el => initTooltip(el));
});
