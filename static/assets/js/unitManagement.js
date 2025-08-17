// tenant_management/static/tenant_management/js/unitManagement.js
document.addEventListener("DOMContentLoaded", function () {
  const modalEl = document.getElementById("crudModal");
  const modalContent = document.getElementById("crudModalContent");

  // Helper to get or create bootstrap instance
  function getModalInstance() {
    if (!modalEl) return null;
    return bootstrap.Modal.getOrCreateInstance(modalEl);
  }

  // Open modal for create/edit/delete (generic)
  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-url]");
    if (!btn) return;

    const url = btn.dataset.url;
    if (!url) return;

    fetch(url, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error("Network response was not ok");
        return r.text();
      })
      .then((html) => {
        if (!modalContent) throw new Error("#crudModalContent not found");
        modalContent.innerHTML = html;
        const instance = getModalInstance();
        if (instance) instance.show();

        // autofocus first field in injected content
        const first = modalContent.querySelector("input, textarea, select");
        if (first) first.focus();
      })
      .catch((err) => {
        console.error("Failed to load modal form:", err);
        showToast("error", "Failed to load form.");
      });
  });

  // Handle form submissions inside modal (delegated)
  modalContent && modalContent.addEventListener("submit", async function (e) {
    // Find the closest form (works if e.target is a button/input)
    const form = e.target.closest ? e.target.closest("form") : null;
    if (!form || !modalContent.contains(form)) return; // ignore unrelated submits

    e.preventDefault(); // stop native submit

    const action = form.getAttribute("action") || window.location.href;
    const method = (form.getAttribute("method") || "post").toUpperCase();
    const formData = new FormData(form);

    // Optional: disable submit buttons to prevent double submits
    const submitButtons = Array.from(form.querySelectorAll('[type="submit"]'));
    submitButtons.forEach(btn => btn.disabled = true);

    try {
      const resp = await fetch(action, {
        method,
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: formData,
        credentials: "same-origin",
      });

      const contentType = (resp.headers.get("content-type") || "").toLowerCase();

      // Case A — JSON (expected AJAX flow)
      if (contentType.includes("application/json")) {
        const data = await resp.json();
        if (data.success) {
          const instance = getModalInstance();
          if (instance) instance.hide();

          showToast("success", data.message || "Saved");

          // If backend gave a redirect url, follow it. Otherwise reload to sync.
          if (data.redirect_url) {
            window.location.href = data.redirect_url;
          } else {
            window.location.reload();
          }
          return;
        } else {
          // server returned validation errors as HTML inside JSON
          modalContent.innerHTML = data.html || "<div class='alert alert-danger'>Validation failed</div>";
          const first = modalContent.querySelector("input, textarea, select");
          if (first) first.focus();
          return;
        }
      }

      // Case B — Not JSON (server might have redirected or returned full HTML)
      const text = await resp.text();

      // If fetch followed a redirect (302) or returned main page HTML, reload to sync UI.
      if (resp.redirected || /<table.*unit|Total Units:|Units Dashboard/i.test(text)) {
        // server did redirect or returned property page — just reload
        window.location.reload();
        return;
      }

      // Otherwise assume it's an HTML fragment (form with errors) — render in modal
      modalContent.innerHTML = text;
      const firstField = modalContent.querySelector("input, textarea, select");
      if (firstField) firstField.focus();

    } catch (err) {
      console.error("AJAX form submit failed:", err);
      showToast("error", "Request failed. See console for details.");
    } finally {
      // re-enable submit buttons
      submitButtons.forEach(btn => btn.disabled = false);
    }
  });

  // Toast helper (same as before, centralized)
  function showToast(level = "info", message = "") {
    let wrapper = document.getElementById("toastWrapper");
    if (!wrapper) {
      wrapper = document.createElement("div");
      wrapper.id = "toastWrapper";
      wrapper.style.position = "fixed";
      wrapper.style.top = "1rem";
      wrapper.style.right = "1rem";
      wrapper.style.zIndex = 1100;
      document.body.appendChild(wrapper);
    }

    const toastId = `toast-${Date.now()}`;
    const bgClass =
      level === "success"
        ? "bg-success text-white"
        : level === "error"
        ? "bg-danger text-white"
        : "bg-secondary text-white";

    wrapper.insertAdjacentHTML(
      "afterbegin",
      `
      <div id="${toastId}" class="toast ${bgClass}" role="alert" aria-live="assertive" aria-atomic="true" data-bs-delay="3500">
        <div class="d-flex">
          <div class="toast-body">${message}</div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
      </div>
    `
    );

    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl);
    toast.show();
    toastEl.addEventListener("hidden.bs.toast", () => toastEl.remove());
  }
});
