// unitManagement.js

// unitManagement.js
document.addEventListener("DOMContentLoaded", function () {
  const modalEl = document.getElementById("crudModal");
  const modalContent = document.getElementById("crudModalContent");
  const bsModal = new bootstrap.Modal(modalEl);

  // Generic loader for any button with data-url
  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-url]");
    if (!btn) return;

    const url = btn.dataset.url;
    fetch(url, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error("Network response was not ok");
        return r.text();
      })
      .then((html) => {
        modalContent.innerHTML = html;
        bsModal.show();
      })
      .catch((err) => {
        console.error("Failed to load modal:", err);
        showToast("error", "Failed to open dialog.");
      });
  });

  // Submit handler for forms loaded inside modalContent
  modalContent.addEventListener("submit", function (e) {
    const form = e.target;
    if (!form || form.tagName.toLowerCase() !== "form") return;

    e.preventDefault();

    const action = form.getAttribute("action") || window.location.href;
    const method = (form.getAttribute("method") || "post").toUpperCase();
    const formData = new FormData(form);

    fetch(action, {
      method: method,
      headers: {
        'X-Requested-With': 'XMLHttpRequest', // important for server to detect AJAX
        // DO NOT set Content-Type; browser will set the multipart/form-data boundary
      },
      body: formData,
      credentials: "same-origin"
    })
      .then(async (resp) => {
        // If server returns JSON success (our delete path) -> handle
        const contentType = resp.headers.get("content-type") || "";
        if (resp.status === 200 && contentType.includes("application/json")) {
          const data = await resp.json();
          if (data.success) {
            // remove row if provided
            if (data.unit_id) {
              const row = document.getElementById(`unit-${data.unit_id}`);
              if (row) row.remove();
            }
            bsModal.hide();
            showToast("success", data.message || "Deleted");
            return;
          }
        }

        // If server sent HTML (form validation errors or non-AJAX fallback), replace modal content
        const text = await resp.text();
        modalContent.innerHTML = text;
      })
      .catch((err) => {
        console.error("Form submission failed:", err);
        showToast("error", "Request failed.");
      });
  });

  // Helper: show bootstrap toast (creates one if not present)
  function showToast(level = "info", message = "") {
    // create container if missing
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
    const bgClass = level === "success" ? "bg-success text-white" :
                    level === "error" ? "bg-danger text-white" : "bg-secondary text-white";

    wrapper.insertAdjacentHTML("afterbegin", `
      <div id="${toastId}" class="toast ${bgClass}" role="alert" aria-live="assertive" aria-atomic="true" data-bs-delay="3500">
        <div class="d-flex">
          <div class="toast-body">
            ${message}
          </div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
      </div>
    `);

    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl);
    toast.show();

    // Remove DOM element when hidden
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
  }

});

