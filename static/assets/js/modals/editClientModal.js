document.addEventListener("DOMContentLoaded", () => {
  /************************************
   *  🔹 Helper Functions
   ************************************/
  function getCookie(name) {
    const v = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return v ? decodeURIComponent(v.pop()) : null;
  }

  function clearFormErrors(form) {
    form
      .querySelectorAll(".invalid-feedback, .field-error")
      .forEach((el) => el.remove());
    form
      .querySelectorAll(".is-invalid")
      .forEach((el) => el.classList.remove("is-invalid"));
    const global = form.querySelector(".form-global-error");
    if (global) global.remove();
  }

  function showGlobalError(form, message) {
    clearFormErrors(form);
    const div = document.createElement("div");
    div.className = "alert alert-danger form-global-error";
    div.role = "alert";
    div.innerHTML = message;
    const container = form.querySelector(".modal-body") || form;
    container.insertBefore(div, container.firstChild);
  }

  function showFieldErrors(form, errors) {
    for (const [field, items] of Object.entries(errors)) {
      const messages = items.map((i) => i.message || i).join("; ");
      if (field === "non_field_errors" || field === "__all__") {
        showGlobalError(form, messages);
        continue;
      }
      const input = form.querySelector(`[name="${field}"]`);
      if (input) {
        input.classList.add("is-invalid");
        const feedback = document.createElement("div");
        feedback.className = "invalid-feedback field-error";
        feedback.innerText = messages;
        const existing = input.parentNode.querySelector(".field-error");
        if (existing) existing.remove();
        input.parentNode.appendChild(feedback);
      } else {
        showGlobalError(form, `${field}: ${messages}`);
      }
    }
  }

  /************************************
   *  🟢 Add Client Form Handler
   ************************************/
  const addClientForm = document.getElementById("client_service_form");

  if (addClientForm) {
    addClientForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      e.stopPropagation(); // ✅ Prevent Bootstrap modal auto-close
      clearFormErrors(addClientForm);

      const submitBtn = addClientForm.querySelector('button[type="submit"]');
      const originalHtml = submitBtn.innerHTML;
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm" role="status"></span> Saving...`;

      const modalEl = document.getElementById("addClientModal");
      const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

      try {
        const res = await fetch(addClientForm.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          body: new FormData(addClientForm),
        });

        const data = await res.json();
        const messageContainer = document.getElementById("message-container");
        if (messageContainer) messageContainer.innerHTML = "";

        // ❌ Validation or server error
        if (!res.ok || data.errors) {
          if (data.errors) showFieldErrors(addClientForm, data.errors);

          if (messageContainer) {
            messageContainer.innerHTML = `
              <div class="alert alert-danger alert-dismissible fade show" role="alert">
                ⚠️ ${data.message || "Please correct the highlighted errors."}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
              </div>`;
          } else {
            showGlobalError(
              addClientForm,
              data.message || "Please correct the highlighted errors."
            );
          }

          // ✅ Explicitly re-show modal
          modal.show();
          return;
        }

        // ✅ Success — show success message
        if (messageContainer) {
          messageContainer.innerHTML = `
            <div class="alert alert-success alert-dismissible fade show" role="alert">
              ✅ ${data.message || "Client added successfully!"}
              <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>`;
        }

        // ✅ Hide modal only on success
        modal.hide();

        // Refresh to show the new client
        setTimeout(() => window.location.reload(), 1500);
      } catch (err) {
        console.error("Add client request failed:", err);
        showGlobalError(addClientForm, "Unexpected error: " + err.message);
        modal.show(); // Keep open if AJAX fails
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalHtml;
      }
    });
  }

  /************************************
   *  🟡 Edit Client Form Handler
   ************************************/
  const editForms = document.querySelectorAll(".edit-client-form");

  editForms.forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      clearFormErrors(form);

      const submitBtn = form.querySelector('button[type="submit"]');
      const originalHtml = submitBtn.innerHTML;
      submitBtn.disabled = true;
      submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm" role="status"></span> Saving...`;

      const modalEl = form.closest(".modal");
      const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

      try {
        const res = await fetch(form.action, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          body: new FormData(form),
        });

        const data = await res.json();
        const messageContainer = document.getElementById("message-container");
        if (messageContainer) messageContainer.innerHTML = "";

        if (!res.ok || data.errors) {
          if (data.errors) showFieldErrors(form, data.errors);

          if (messageContainer) {
            messageContainer.innerHTML = `
            <div class="alert alert-danger alert-dismissible fade show" role="alert">
              ⚠️ ${data.message || "Please correct the highlighted errors."}
              <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>`;
          } else {
            showGlobalError(
              form,
              data.message || "Please correct the highlighted errors."
            );
          }

          modal.show(); // ✅ Keep modal open if error
          return;
        }

        // ✅ Success
        if (messageContainer) {
          messageContainer.innerHTML = `
          <div class="alert alert-success alert-dismissible fade show" role="alert">
            ✅ ${data.message || "Client updated successfully!"}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
          </div>`;
        }

        modal.hide();
        setTimeout(() => window.location.reload(), 1500);
      } catch (err) {
        console.error("Edit client request failed:", err);
        showGlobalError(form, "Unexpected error: " + err.message);
        modal.show();
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalHtml;
      }
    });
  });
  
});
