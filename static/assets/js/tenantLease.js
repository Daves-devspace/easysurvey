// tenantLease.js
document.addEventListener("DOMContentLoaded", function () {
  const MODAL_SELECTOR = "#combinedModal";
  const MODAL_BODY = `${MODAL_SELECTOR} .modal-body`;
  const MODAL_TITLE = `${MODAL_SELECTOR} .modal-title`;
  const ADD_BTN_SELECTOR = ".btn-add-tenant";

  async function bindButtons(force = false) {
    document.querySelectorAll(ADD_BTN_SELECTOR).forEach(btn => {
      if (!force && btn._boundTenant) return;
      btn._boundTenant = true;

      btn.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const url = btn.dataset.url;
        const unitId = btn.dataset.unitId;
        const unitNumber = btn.dataset.unitNumber;
        if (!url) {
          console.warn("Add Tenant: data-url missing");
          return;
        }

        try {
          const res = await fetch(url, { credentials: "same-origin" });
          if (!res.ok) throw new Error(`Load failed: ${res.status}`);
          const payload = await res.json(); // expects { html: '...' }

          const modalBodyEl = document.querySelector(MODAL_BODY);
          if (!modalBodyEl) throw new Error("Modal body not found in DOM");

          modalBodyEl.innerHTML = payload.html;

          // If server didn't return a form (e.g. rendering error), log it
          const form = modalBodyEl.querySelector("form#combinedForm") || modalBodyEl.querySelector("form");
          if (!form) {
            console.error("tenantLease: server returned no form. payload.html:", payload.html);
            showToast("Server returned unexpected HTML. Check console.", "danger");
            return;
          }

          // Defensive: set hidden inputs and displayed unit
          const unitInput = modalBodyEl.querySelector("#combined-unit-input");
          if (unitInput) unitInput.value = unitId;

          const propInput = modalBodyEl.querySelector("#combined-property-input");
          if (propInput && !propInput.value && btn.dataset.propertyId) {
            propInput.value = btn.dataset.propertyId;
          }

          const unitDisplay = modalBodyEl.querySelector("#combined-unit-display");
          if (unitDisplay) unitDisplay.textContent = `Unit ${unitNumber || unitId}`;

          // set form action to POST to the same URL
          form.setAttribute("action", url);

          // Remove any older handler reference and add the new one
          form.removeEventListener("submit", handleSubmit);
          form.addEventListener("submit", handleSubmit);

          // update modal title and show
          const header = document.querySelector(MODAL_TITLE);
          if (header) header.textContent = `Add Tenant & Lease — Unit ${unitNumber || unitId}`;

          const modalEl = document.querySelector(MODAL_SELECTOR);
          bootstrap.Modal.getOrCreateInstance(modalEl).show();

          // autofocus first input
          const first = modalBodyEl.querySelector("input, textarea, select");
          if (first) first.focus();
        } catch (err) {
          console.error("tenantLease open error:", err);
          showToast("Failed to load form. See console.", "danger");
        }
      });
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const modalEl = document.querySelector(MODAL_SELECTOR);
    const submitBtn = form.querySelector('button[type="submit"]');
    const origText = submitBtn ? submitBtn.innerHTML : null;

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = "Saving…";
    }

    try {
      const url = form.getAttribute("action") || window.location.href;
      const formData = new FormData(form);
      const res = await fetch(url, {
        method: form.method || "POST",
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: formData,
      });

      const data = await res.json();

      if (data.success) {
        // Hide modal first
        const bs = bootstrap.Modal.getInstance(modalEl);
        if (bs) bs.hide();

        // Handle redirect
        if (data.redirect) {
          // Show success message briefly before redirect
          if (data.message) {
            showToast(data.message, "success");
          }
          
          // Redirect after a short delay to show the toast
          setTimeout(() => {
            window.location.href = data.redirect;
          }, 1000);
        } else {
          // Fallback: reload current page
          showToast(data.message || "Saved successfully", "success");
          setTimeout(() => {
            location.reload();
          }, 1000);
        }
      } else {
        // inject returned form-with-errors
        if (data.html) {
          const modalBodyEl = modalEl.querySelector(".modal-body");
          modalBodyEl.innerHTML = data.html;
          const newForm = modalBodyEl.querySelector("form#combinedForm") || modalBodyEl.querySelector("form");
          if (newForm) newForm.addEventListener("submit", handleSubmit);
        } else {
          showToast("Validation failed. Fix the errors and try again.", "danger");
        }
      }
    } catch (err) {
      console.error("tenantLease submit error:", err);
      showToast("Save failed. See console.", "danger");
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        if (origText !== null) submitBtn.innerHTML = origText;
      }
    }
  }

  function showToast(message, type = "success") {
    const t = document.createElement("div");
    t.className = `toast align-items-center text-white bg-${type} border-0 show`;
    t.style.position = "fixed";
    t.style.top = "1rem";
    t.style.right = "1rem";
    t.style.zIndex = 9999;
    t.innerHTML = `<div class="d-flex"><div class="toast-body">${message}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
    document.body.appendChild(t);
    setTimeout(() => { if (t.parentNode) t.remove(); }, 4000);
  }

  // expose rebind for other code
  window.TenantLease = { rebind: () => bindButtons(true) };

  // init
  bindButtons();
});