document.addEventListener("DOMContentLoaded", () => {
  // ── Action modals & buttons ───────────────────────────────────────────────
  const declineModalEl = document.getElementById("taskDeclineModal");
  const extendModalEl = document.getElementById("taskExtendModal");
  const declineSummaryEl = document.getElementById("taskDeclineSummary");
  const declineReasonEl = document.getElementById("taskDeclineReason");
  const confirmDeclineBtn = document.getElementById("confirmTaskDecline");
  const extendSummaryEl = document.getElementById("taskExtendSummary");
  const extendDaysEl = document.getElementById("taskExtendDays");
  const extendReasonEl = document.getElementById("taskExtendReason");
  const confirmExtendBtn = document.getElementById("confirmTaskExtend");

  let declineUrl = "";
  let extendUrl = "";

  document.querySelectorAll(".task-accept-action").forEach((button) => {
    button.addEventListener("click", async () => {
      const taskName = button.dataset.taskName || "this task";
      const clientName = button.dataset.clientName || "the client";
      if (!window.confirm(`Accept ${taskName} for ${clientName}?`)) {
        return;
      }

      await submitTaskAction({
        url: button.dataset.url,
        body: "reason=Accepted from tasks page",
        successPrefix: "Success: ",
        fallbackError: "Failed to accept task.",
      });
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
        successPrefix: "Success: ",
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
      successPrefix: "Success: ",
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
      window.alert("Please enter a valid number of additional days (1-30).");
      return;
    }

    if (!reason) {
      window.alert("Please provide a reason for the deadline extension.");
      return;
    }

    await submitTaskAction({
      url: extendUrl,
      body: `additional_days=${days}&reason=${encodeURIComponent(reason)}`,
      successPrefix: "Success: ",
      fallbackError: "Failed to request deadline extension.",
      onSuccess: () => {
        const modal = bootstrap.Modal.getInstance(extendModalEl);
        modal?.hide();
      },
    });
  });

  async function submitTaskAction({
    url,
    body,
    successPrefix,
    fallbackError,
    onSuccess,
  }) {
    if (!url) {
      window.alert(fallbackError);
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

      const data = await response.json();
      if (!response.ok || !data.success) {
        window.alert(`Error: ${data.message || fallbackError}`);
        return;
      }

      if (typeof onSuccess === "function") {
        onSuccess();
      }
      window.alert(`${successPrefix}${data.message}`);
      window.location.reload();
    } catch (error) {
      console.error(error);
      window.alert(fallbackError);
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
