// tenant_management/static/tenant_management/js/modals.js

// Handle click on "Add Tenant" button
document.addEventListener("click", async function (e) {
  const btn = e.target.closest(".btn-add-tenant");
  if (!btn) return;

  e.preventDefault();

  try {
    const url = btn.dataset.url;
    if (!url) throw new Error("No data-url attribute found on button.");

    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`Failed to load: ${res.statusText}`);

    const html = await res.text();
    const modalEl = document.getElementById("tenantLeaseModal");
    const bodyEl = document.getElementById("tenantLeaseModalBody");
    const titleEl = modalEl.querySelector(".modal-title");

    bodyEl.innerHTML = html;
    if (btn.dataset.unitNumber) {
      titleEl.textContent = `Assign Tenant — Unit ${btn.dataset.unitNumber}`;
    }

    bootstrap.Modal.getOrCreateInstance(modalEl).show();

    // Autofocus first input field
    const firstInput = modalEl.querySelector("input, textarea, select");
    if (firstInput) firstInput.focus();
  } catch (err) {
    console.error("Failed to load tenant lease form", err);
    alert("Unable to load form. Please try again later.");
  }
});

// Handle form submission inside modal
document.addEventListener("submit", async function (e) {
  const form = e.target.form || e.target.closest("form");
  const modalEl = document.getElementById("tenantLeaseModal");
  if (!form || !modalEl.contains(form)) return;

  e.preventDefault();

  const formData = new FormData(form);

  try {
    const res = await fetch(form.action, {
      method: "POST",
      body: formData,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    });

    const data = await res.json();

    if (data.success) {
      bootstrap.Modal.getInstance(modalEl).hide();
      if (data.message) alert(data.message);
      if (data.redirect_url) {
        window.location.href = data.redirect_url;
      } else {
        window.location.reload();
      }
    } else {
      // Replace modal body with updated form HTML (contains errors)
      document.getElementById("tenantLeaseModalBody").innerHTML = data.html;

      // Re-run autofocus for first input after error re-render
      const firstInput = modalEl.querySelector("input, textarea, select");
      if (firstInput) firstInput.focus();
    }
  } catch (err) {
    console.error("Form submission failed", err);
    alert("An unexpected error occurred. Please try again.");
  }
});
