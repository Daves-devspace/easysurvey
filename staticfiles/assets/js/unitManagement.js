/**
 * Unified CRUD Management System
 * Handles modal operations for Tenants, Units, and Leases
 */
const CRUDManager = (() => {
  const config = {
    modalId: "crudModal",
    modalContentId: "crudModalContent",
    deleteModalId: "deleteModal",
    deleteModalContentId: "deleteModalContent",
    messageContainerId: "messageContainer",
    csrfToken: document.querySelector("[name=csrfmiddlewaretoken]")?.value,
  };

  function init() {
    bindGlobalEvents();
    console.log("CRUD Manager initialized");
  }

  function bindGlobalEvents() {
    document.removeEventListener("click", globalClickHandler);
    document.removeEventListener("submit", globalSubmitHandler);

    document.addEventListener("click", globalClickHandler);
    document.addEventListener("submit", globalSubmitHandler);
  }

  function globalClickHandler(e) {
    const btn = e.target.closest("[data-crud-action]");
    if (!btn) return;

    e.preventDefault();
    const action = btn.dataset.crudAction;
    const url = btn.dataset.crudUrl;
    const title = btn.dataset.title || `${capitalize(action)} Item`;

    if (action === "create" || action === "edit") {
      openCrudModal(url, title);
    } else if (action === "delete") {
      openDeleteModal(url, title);
    }
  }

  function globalSubmitHandler(e) {
    const form = e.target.closest("form");
    if (!form) return;

    if (form.closest(`#${config.modalContentId}`)) {
      e.preventDefault();
      submitForm(form);
    } else if (form.closest(`#${config.deleteModalContentId}`)) {
      e.preventDefault();
      submitDeleteForm(form);
    }
  }

  async function openCrudModal(url, title) {
    try {
      const response = await fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok)
        throw new Error(`HTTP error! status: ${response.status}`);

      const contentType = response.headers.get("content-type");
      const htmlContent = contentType?.includes("application/json")
        ? (await response.json()).html
        : await response.text();

      document.getElementById(config.modalContentId).innerHTML = htmlContent;

      const modal = new bootstrap.Modal(
        document.getElementById(config.modalId)
      );
      modal.show();

      const modalTitle = document.querySelector(
        `#${config.modalId} .modal-title`
      );
      if (modalTitle) modalTitle.textContent = title;
    } catch (error) {
      console.error("Error opening CRUD modal:", error);
      showMessage("Error loading form", "danger");
    }
  }

  async function submitForm(form) {
    const formData = new FormData(form);
    const url = form.action;
    const method = form.method;

    try {
      const response = await fetch(url, {
        method,
        body: formData,
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": config.csrfToken,
        },
      });

      if (response.redirected) return (window.location.href = response.url);

      let data;
      try {
        data = await response.json();
      } catch {
        const html = await response.text();
        document.getElementById(config.modalContentId).innerHTML = html;
        bindGlobalEvents();
        return;
      }

      if (data.success) {
        bootstrap.Modal.getInstance(
          document.getElementById(config.modalId)
        )?.hide();
        showMessage(data.message, "success");
        data.row_id && data.html && updateUI(data.row_id, data.html);
      } else {
        data.html &&
          (document.getElementById(config.modalContentId).innerHTML =
            data.html);
        bindGlobalEvents();
        !data.html && showMessage(data.error || "Operation failed", "danger");
      }
    } catch (error) {
      console.error("Error submitting form:", error);
      showMessage("An error occurred. Please try again.", "danger");
    }
  }

  async function openDeleteModal(url, title) {
    try {
      const response = await fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok)
        throw new Error(`HTTP error! status: ${response.status}`);

      const contentType = response.headers.get("content-type");
      const htmlContent = contentType?.includes("application/json")
        ? (await response.json()).html
        : await response.text();

      document.getElementById(config.deleteModalContentId).innerHTML =
        htmlContent;
      const modal = new bootstrap.Modal(
        document.getElementById(config.deleteModalId)
      );
      modal.show();

      const modalTitle = document.querySelector(
        `#${config.deleteModalId} .modal-title`
      );
      if (modalTitle) modalTitle.textContent = title;
    } catch (error) {
      console.error("Error opening delete modal:", error);
      showMessage("Error loading confirmation", "danger");
    }
  }

  async function submitDeleteForm(form) {
    const formData = new FormData(form);
    const url = form.action;

    try {
      const response = await fetch(url, {
        method: "POST",
        body: formData,
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": config.csrfToken,
        },
      });

      let data;
      try {
        data = await response.json();
      } catch {
        return showMessage("Unexpected response. Refresh page.", "warning");
      }

      if (data.success) {
        bootstrap.Modal.getInstance(
          document.getElementById(config.deleteModalId)
        )?.hide();
        showMessage(data.message, "success");
        data.row_id && removeFromUI(data.row_id);
      } else showMessage(data.error || "Failed to delete item", "danger");
    } catch (error) {
      console.error("Error deleting item:", error);
      showMessage("An error occurred. Please try again.", "danger");
    }
  }

  function updateUI(rowId, html) {
    const existingRow = document.getElementById(rowId);
    if (existingRow) existingRow.outerHTML = html;
    else {
      const tableBody = document.querySelector(
        `#${rowId.split("-")[0]}s-table tbody, [data-table-type="${
          rowId.split("-")[0]
        }s"] tbody`
      );
      if (tableBody) tableBody.insertAdjacentHTML("beforeend", html);
      else showMessage("Could not update interface. Refresh page.", "warning");
    }
    bindGlobalEvents();
  }

  function removeFromUI(rowId) {
    const el = document.getElementById(rowId);
    el && el.remove();
  }

  function showMessage(message, type = "info") {
    const container = document.getElementById(config.messageContainerId);
    if (!container) return console.error("Message container not found");

    container.innerHTML = "";
    const alertTypeMap = {
      success: "alert-success",
      error: "alert-danger",
      danger: "alert-danger",
      warning: "alert-warning",
      info: "alert-info",
    };
    const alertHtml = `
      <div id="alert-${Date.now()}" class="alert ${
      alertTypeMap[type] || "alert-secondary"
    } alert-dismissible fade show" role="alert">
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
      </div>
    `;
    container.insertAdjacentHTML("beforeend", alertHtml);
    setTimeout(() => {
      const alertEl = container.querySelector(".alert");
      alertEl && bootstrap.Alert.getOrCreateInstance(alertEl).close();
    }, 5000);
  }

  function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
  }

  return { init, showMessage, openCrudModal, openDeleteModal };
})();

document.addEventListener("DOMContentLoaded", CRUDManager.init);
