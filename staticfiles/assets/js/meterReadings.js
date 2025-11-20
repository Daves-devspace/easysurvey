// static/assets/js/meterReadings.js
document.addEventListener("DOMContentLoaded", () => {
  const modalEl = document.getElementById("meterReadingModal");
  if (!modalEl) return;
  const modal = new bootstrap.Modal(modalEl);
  const modalBody = modalEl.querySelector(".modal-content");
  const msgContainer = document.getElementById("messageContainer");

  document.body.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".open-meter-modal");
    if (!btn) return;
    ev.preventDefault();

    try {
      const res = await fetch(btn.dataset.url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!res.ok) throw new Error("Failed to fetch form");
      modalBody.innerHTML = await res.text();
      modal.show();
      bindModalForm(modal, modalBody);
    } catch (err) {
      console.error("Error loading form", err);
      if (msgContainer) msgContainer.innerHTML = `<div class="alert alert-danger">Failed to load form.</div>`;
    }
  });

  function bindModalForm(modal, modalBody) {
    const form = modalBody.querySelector("form");
    if (!form) return;

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const res = await fetch(form.action, {
          method: form.method || "POST",
          body: new FormData(form),
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        const data = await res.json();

        if (data.messages && msgContainer) {
          msgContainer.innerHTML = data.messages; // update messages block
        }

        if (data.success) {
          const unitRowId = `reading-row-${data.unit_id}`;
          const existing = document.getElementById(unitRowId);
          const temp = document.createElement("tbody");
          temp.innerHTML = data.row_html.trim();
          const newRow = temp.firstElementChild;

          if (existing) {
            existing.replaceWith(newRow);
          } else {
            const table = document.querySelector("#meter-readings-table tbody");
            if (table) table.appendChild(newRow);
          }
          modal.hide();
        } else {
          if (data.form_html) {
            modalBody.innerHTML = data.form_html;
            bindModalForm(modal, modalBody);
          }
        }
      } catch (err) {
        console.error("Error submitting form", err);
        if (msgContainer) msgContainer.innerHTML = `<div class="alert alert-danger">Error saving reading.</div>`;
      }
    });
  }
});
