document.addEventListener("DOMContentLoaded", () => {
  // ── Action modals & buttons ───────────────────────────────────────────────
  const acceptModalEl = document.getElementById("taskAcceptModal");
  const declineModalEl = document.getElementById("taskDeclineModal");
  const extendModalEl = document.getElementById("taskExtendModal");
  const acceptSummaryEl = document.getElementById("taskAcceptSummary");
  const confirmAcceptBtn = document.getElementById("confirmTaskAccept");
  const declineSummaryEl = document.getElementById("taskDeclineSummary");
  const declineReasonEl = document.getElementById("taskDeclineReason");
  const confirmDeclineBtn = document.getElementById("confirmTaskDecline");
  const extendSummaryEl = document.getElementById("taskExtendSummary");
  const extendDaysEl = document.getElementById("taskExtendDays");
  const extendReasonEl = document.getElementById("taskExtendReason");
  const confirmExtendBtn = document.getElementById("confirmTaskExtend");
  const actionResultModalEl = document.getElementById("taskActionResultModal");
  const actionResultHeaderEl = document.getElementById("taskActionResultHeader");
  const actionResultTitleEl = document.getElementById("taskActionResultTitle");
  const actionResultMessageEl = document.getElementById("taskActionResultMessage");

  let acceptUrl = "";
  let declineUrl = "";
  let extendUrl = "";

  function showTaskActionResult({ success, message, reloadOnClose = false }) {
    if (
      !actionResultModalEl ||
      !actionResultHeaderEl ||
      !actionResultTitleEl ||
      !actionResultMessageEl
    ) {
      if (success && reloadOnClose) {
        window.location.reload();
      }
      return;
    }

    actionResultHeaderEl.classList.remove("bg-success", "bg-danger", "text-white");
    actionResultHeaderEl.classList.add(success ? "bg-success" : "bg-danger", "text-white");
    actionResultTitleEl.textContent = success ? "Success" : "Action Failed";
    actionResultMessageEl.textContent = message;

    const resultModal = bootstrap.Modal.getOrCreateInstance(actionResultModalEl);
    if (success && reloadOnClose) {
      const reloadHandler = () => {
        actionResultModalEl.removeEventListener("hidden.bs.modal", reloadHandler);
        window.location.reload();
      };
      actionResultModalEl.addEventListener("hidden.bs.modal", reloadHandler);
    }

    resultModal.show();
  }

  document.querySelectorAll(".task-accept-action").forEach((button) => {
    button.addEventListener("click", () => {
      acceptUrl = button.dataset.url || "";
      if (acceptSummaryEl) {
        const taskName = button.dataset.taskName || "Task";
        const clientName = button.dataset.clientName || "client";
        acceptSummaryEl.textContent = `Accept ${taskName} for ${clientName}?`;
      }
    });
  });

  confirmAcceptBtn?.addEventListener("click", async () => {
    await submitTaskAction({
      url: acceptUrl,
      body: "reason=Accepted from tasks page",
      fallbackError: "Failed to accept task.",
      onSuccess: () => {
        const modal = bootstrap.Modal.getInstance(acceptModalEl);
        modal?.hide();
      },
    });
  });

  document.querySelectorAll(".task-complete-action").forEach((button) => {
    button.addEventListener("click", async () => {
      const taskName = button.dataset.taskName || "this task";
      const clientName = button.dataset.clientName || "the client";
      if (
        !window.confirm(
          `Complete your process assignment for ${taskName} (${clientName})?`,
        )
      ) {
        return;
      }

      await submitTaskAction({
        url: button.dataset.url,
        body: "note=Completed from tasks page",
        fallbackError: "Failed to complete process assignment.",
      });
    });
  });

  document.querySelectorAll(".task-decline-action").forEach((button) => {
    button.addEventListener("click", () => {
      declineUrl = button.dataset.url || "";
      if (declineSummaryEl) {
        declineSummaryEl.textContent = `${button.dataset.taskName || "Task"} for ${button.dataset.clientName || "client"}`;
      }
      if (declineReasonEl) {
        declineReasonEl.value = "";
      }
    });
  });

  confirmDeclineBtn?.addEventListener("click", async () => {
    const reason = declineReasonEl?.value?.trim() || "";
    await submitTaskAction({
      url: declineUrl,
      body: `reason=${encodeURIComponent(reason)}`,
      fallbackError: "Failed to decline task.",
      onSuccess: () => {
        const modal = bootstrap.Modal.getInstance(declineModalEl);
        modal?.hide();
      },
    });
  });

  document.querySelectorAll(".task-extend-action").forEach((button) => {
    button.addEventListener("click", () => {
      extendUrl = button.dataset.url || "";
      if (extendSummaryEl) {
        const deadline = button.dataset.currentDeadline || "No deadline";
        extendSummaryEl.textContent = `${button.dataset.taskName || "Task"} for ${button.dataset.clientName || "client"} · Current deadline: ${deadline}`;
      }
      if (extendDaysEl) {
        extendDaysEl.value = 7;
      }
      if (extendReasonEl) {
        extendReasonEl.value = "";
      }
    });
  });

  confirmExtendBtn?.addEventListener("click", async () => {
    const days = parseInt(extendDaysEl?.value || "0", 10);
    const reason = extendReasonEl?.value?.trim() || "";

    if (!days || days < 1 || days > 30) {
      showTaskActionResult({
        success: false,
        message: "Please enter a valid number of additional days (1-30).",
      });
      return;
    }

    if (!reason) {
      showTaskActionResult({
        success: false,
        message: "Please provide a reason for the deadline extension.",
      });
      return;
    }

    await submitTaskAction({
      url: extendUrl,
      body: `additional_days=${days}&reason=${encodeURIComponent(reason)}`,
      fallbackError: "Failed to request deadline extension.",
      onSuccess: () => {
        const modal = bootstrap.Modal.getInstance(extendModalEl);
        modal?.hide();
      },
    });
  });

  async function submitTaskAction({ url, body, fallbackError, onSuccess }) {
    if (!url) {
      showTaskActionResult({ success: false, message: fallbackError });
      return;
    }

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCookie("csrftoken"),
          "X-Requested-With": "XMLHttpRequest",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
      });

      let data = {};
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        data = await response.json();
      } else {
        const text = await response.text();
        data = { message: text || fallbackError };
      }

      if (!response.ok || !data.success) {
        showTaskActionResult({
          success: false,
          message: data.message || fallbackError,
        });
        return;
      }

      if (typeof onSuccess === "function") {
        onSuccess();
      }

      showTaskActionResult({
        success: true,
        message: data.message || "Action completed successfully.",
        reloadOnClose: true,
      });
    } catch (error) {
      console.error(error);
      showTaskActionResult({ success: false, message: fallbackError });
    }
  }

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i += 1) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === `${name}=`) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }
});
