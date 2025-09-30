// bookingmodals.js (unified create/edit modal + shared auto-message logic)
document.addEventListener('DOMContentLoaded', () => {
  const DEBUG = false; // set true while debugging

  // Function to decode Unicode escape sequences
  function decodeUnicodeEscapes(str) {
    if (!str) return str;
    return str.replace(/\\u[\dA-F]{4}/gi, function(match) {
      return String.fromCharCode(parseInt(match.replace(/\\u/g, ''), 16));
    });
  }

  // Function to safely encode for HTML attributes
  function safeAttributeEncode(str) {
    if (!str) return '';
    return String(str)
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // Show message function
  function showMessage(message, type = 'info') {
    const messageContainer = document.getElementById('booking-messages');
    if (!messageContainer) return;
    
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    messageContainer.appendChild(alertDiv);
    
    // Auto-remove after 5 seconds for success messages
    if (type === 'success') {
        setTimeout(() => {
            if (alertDiv.parentNode) {
                alertDiv.remove();
            }
        }, 5000);
    }
  }

  // Format form errors for display
  function formatFormErrors(errors) {
    if (typeof errors === 'string') return errors;
    
    let errorMessages = [];
    for (const field in errors) {
        if (Array.isArray(errors[field])) {
            errorMessages.push(...errors[field]);
        } else if (typeof errors[field] === 'string') {
            errorMessages.push(errors[field]);
        }
    }
    return errorMessages.join('<br>');
  }

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
    
    // Decode any Unicode escapes in the message
    const decodedMessage = decodeUnicodeEscapes(booking.dispatch_message || '');
    
    tr.innerHTML = `
      <td>${tbody.rows.length + 1}</td>
      <td>${booking.scheduled_date}</td>
      <td>
        <span id="msg-preview-${booking.id}" class="preview-with-tooltip"
              data-bs-toggle="tooltip" title="${safeAttributeEncode(decodedMessage)}">
          ${decodedMessage.slice(0,60)}${decodedMessage.length > 60 ? '…' : ''}
        </span>
      </td>
      <td class="text-end">
        <button
          type="button"
          class="btn btn-sm btn-outline-secondary open-booking-modal"
          data-action="edit"
          data-url="/booking/${booking.id}/edit/"
          data-booking-id="${booking.id}"
          data-scheduled="${booking.scheduled_date}"
          data-message="${safeAttributeEncode(decodedMessage)}"
          data-client-name="${table.dataset.clientName || ''}"
          data-service-name="${table.dataset.serviceName || ''}"
          data-service-id="${table.dataset.serviceId || ''}">
          <i class="fas fa-edit"></i> Edit
        </button>
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
        // Decode any Unicode escapes in the message
        const decodedMessage = decodeUnicodeEscapes(dispatch_message);
        const short = decodedMessage.length > 60 ? decodedMessage.slice(0, 60) + '…' : decodedMessage;
        previewSpan.textContent = short;
        previewSpan.setAttribute('title', decodedMessage);
        initTooltip(previewSpan);
    }
    return true;
  }

  // Add reset template functionality
  function setupResetTemplateButton() {
    document.querySelectorAll('.reset-template-btn').forEach(btn => {
      btn.addEventListener('click', function() {
        const form = this.closest('form');
        const dateInput = form.querySelector('.scheduled-date-input');
        const msgInput = form.querySelector('.message-input');
        
        const realSvcName = resolveName(form.dataset.serviceName, form.dataset.serviceId, 'service');
        const realClientName = resolveName(form.dataset.clientName, form.dataset.serviceId, 'client');
        const isGround = form.dataset.isGround === 'true';
        
        if (dateInput.value) {
          const { full } = buildDispatchMessage(realClientName, realSvcName, dateInput.value, isGround);
          msgInput.value = full;
          delete msgInput.dataset.userEdited;
        }
      });
    });
  }

  // Initialize booking form (modal or inline)
  function initBookingForm(form) {
    if (!form) return;

    const serviceId = form.dataset.serviceId || null;
    const bookingId = form.dataset.bookingId || null;
    const isGround = form.dataset.isGround === 'true';

    const dateInput = form.querySelector('.scheduled-date-input');
    const msgInput = form.querySelector('.message-input');
    const previewSpan = bookingId ? document.getElementById(`msg-preview-${bookingId}`) : null;

    if (DEBUG) console.log('initBookingForm resolved', { serviceId, bookingId, isGround });

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
      
      if (DEBUG) {
        console.log('=== BOOKING FORM SUBMISSION DEBUG ===');
        console.log('Form action:', form.action);
        console.log('Form data entries:');
        for (let [key, value] of fd.entries()) {
          console.log(`  ${key}:`, value);
        }
      }

      fetch(form.action, {
        method: 'POST',
        headers: { 
          'X-CSRFToken': fd.get('csrfmiddlewaretoken'),
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: fd
      })
      .then(response => {
        if (DEBUG) {
          console.log('=== RESPONSE DEBUG ===');
          console.log('Status:', response.status, response.statusText);
          console.log('Redirected:', response.redirected);
        }
        
        // Check if we got a redirect
        if (response.redirected) {
          throw new Error(`Request was redirected to: ${response.url}`);
        }
        
        // Check if response is JSON
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
          return response.text().then(text => {
            throw new Error(`Expected JSON but got: ${contentType}. Body: ${text.substring(0, 200)}`);
          });
        }
        
        return response.json();
      })
      .then(data => {
        if (DEBUG) console.log('=== SUCCESS RESPONSE ===', data);
        
        if (!data) {
          showMessage('Empty response from server.', 'danger');
          return;
        }

        if (data.success) {
          const isEdit = !!form.dataset.bookingId;
          const action = isEdit ? 'updated' : 'created';
          
          // Show success message
          showMessage(`Booking successfully ${action}!`, 'success');
          
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
            if (msgInput) delete msgInput.dataset.userEdited;
            delete form.dataset.bookingId;
          }
        } else {
          // Show error message with details
          const errorMsg = data.error || formatFormErrors(data.errors) || 'Save failed. Please check your input.';
          showMessage(errorMsg, 'danger');
        }
      })
      .catch(err => { 
        console.error('=== FETCH ERROR ===', err); 
        showMessage('Network error: ' + err.message, 'danger');
      });
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
        if (DEBUG) {
          console.log("Raw button dataset:", btn.dataset);
          console.log("clientName from dataset:", btn.dataset.clientName);
        }
        
        const { action, url, clientName, serviceName, serviceId, bookingId, scheduled, message, isGround } = btn.dataset;

        modalForm.action = url;
        modalForm.dataset.clientName = resolveName(clientName, serviceId, 'client');
        modalForm.dataset.serviceName = resolveName(serviceName, serviceId, 'service');
        modalForm.dataset.serviceId = serviceId;
        modalForm.dataset.isGround = isGround === 'true';

        if (action === 'edit' && bookingId) {
          modalForm.dataset.bookingId = bookingId;
          schedInput.value = scheduled || '';
          
          // Decode the message from the button data attribute
          const decodedMessage = decodeUnicodeEscapes(message || '');
          msgInput.value = decodedMessage;
          
          // Only set userEdited if message doesn't match template pattern
          const realSvcName = resolveName(serviceName, serviceId, 'service');
          const realClientName = resolveName(clientName, serviceId, 'client');
          const { full: expectedTemplate } = buildDispatchMessage(realClientName, realSvcName, scheduled, isGround === 'true');
          
          // If message differs from template, mark as user-edited
          if (decodedMessage && decodedMessage !== expectedTemplate) {
            msgInput.dataset.userEdited = 'true';
          }
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

  // Setup reset template buttons
  setupResetTemplateButton();

  // Init inline forms + tooltips
  document.querySelectorAll('.booking-form:not(#bookingModal form.booking-form)').forEach(f => initBookingForm(f));
  document.querySelectorAll('.preview-with-tooltip').forEach(el => initTooltip(el));
});