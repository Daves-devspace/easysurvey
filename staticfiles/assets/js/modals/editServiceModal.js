import {
  loadServicesByCategory,
  loadProcessesForService,
  recalculateTotal,
} from "../utils/serviceUtils.js";

function parseProcessAssigneeMap(rawValue) {
  const mapping = {};
  if (!rawValue) {
    return mapping;
  }

  rawValue
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((entry) => {
      const [processIdRaw, usersRaw = ""] = entry.split(":");
      const processId = String(processIdRaw || "").trim();
      if (!processId) {
        return;
      }

      const users = usersRaw
        .split("|")
        .map((item) => String(item || "").trim())
        .filter(Boolean);

      mapping[processId] = users;
    });

  return mapping;
}

function ensureOption(selectEl, value, label = "") {
  if (!selectEl) {
    return;
  }

  const normalizedValue = String(value || "").trim();
  if (!normalizedValue) {
    return;
  }

  const existingOption = Array.from(selectEl.options || []).find(
    (option) => String(option.value || "").trim() === normalizedValue,
  );

  if (existingOption) {
    return;
  }

  const option = document.createElement("option");
  option.value = normalizedValue;
  option.textContent = label || normalizedValue;
  selectEl.appendChild(option);
}

function syncEnhancedSelect(selectEl, value, label = "") {
  if (!selectEl) {
    return;
  }

  const normalizedValue = String(value || "").trim();
  ensureOption(selectEl, normalizedValue, label);
  selectEl.value = normalizedValue;

  if (
    typeof window.$ !== "undefined" &&
    typeof window.$.fn.select2 !== "undefined"
  ) {
    const $select = window.$(selectEl);
    if ($select.hasClass("select2-hidden-accessible")) {
      $select.val(normalizedValue).trigger("change.select2");
    }
  }
}

function resetEditModal(modal) {
  if (!modal) {
    return;
  }

  const processBody = modal.querySelector(".processTableBody");
  if (processBody) {
    processBody.innerHTML = "";
  }

  modal.querySelectorAll(".totalCost").forEach((el) => {
    el.textContent = "0.00";
  });

  const processSection = modal.querySelector(".processCostSection");
  if (processSection) {
    processSection.style.display = "none";
  }

  const totalOverrideSection = modal.querySelector(".totalPriceOverride");
  if (totalOverrideSection) {
    totalOverrideSection.style.display = "none";
  }

  const groundFields = modal.querySelector(".ground-fields");
  if (groundFields) {
    groundFields.style.display = "none";
  }

  const serviceSelect = modal.querySelector("#editService");
  if (serviceSelect) {
    serviceSelect.innerHTML = '<option value="">Search or select service</option>';
    syncEnhancedSelect(serviceSelect, "");
  }

  syncEnhancedSelect(modal.querySelector("#editAssignedEmployee"), "");

  const overrideInput = modal.querySelector("#editOverrideTotalPrice");
  if (overrideInput) {
    overrideInput.value = "";
  }

  const dateInput = modal.querySelector("[name='scheduled_date']");
  if (dateInput) {
    dateInput.value = "";
  }

  const previewInput = modal.querySelector("[name='dispatch_preview']");
  if (previewInput) {
    previewInput.value = "";
    delete previewInput.dataset.userEdited;
  }
}

export function bindEditService(btn) {
  btn.addEventListener("click", async () => {
    const modal = document.getElementById("editClientServiceModal");
    if (!modal) {
      return;
    }

    resetEditModal(modal);

    const {
      serviceId,
      serviceName,
      category,
      clientServiceId,
      landDescription,
      overrideTotal,
      psIds,
      psCosts,
      psOnboardingIds,
      psAssignees,
      scheduledDate,
      dispatchPreview,
      assignedEmployeeId,
      assignedEmployeeName,
      expectedDurationDays,
    } = btn.dataset;

    modal.querySelector("#editClientServiceId").value = clientServiceId || "";
    modal.querySelector("#editLandDescription").value = landDescription || "";
    modal.querySelector("#editClientName").value = btn.dataset.clientName || "";
    modal.querySelector("#editClientPhone").value = btn.dataset.clientPhone || "";

    const assignedEmployeeInput = modal.querySelector("#editAssignedEmployee");
    syncEnhancedSelect(
      assignedEmployeeInput,
      assignedEmployeeId || "",
      assignedEmployeeName || "",
    );

    const expectedDurationInput = modal.querySelector(
      "#editExpectedDurationDays",
    );
    if (expectedDurationInput) {
      expectedDurationInput.value = expectedDurationDays || "";
      expectedDurationInput.dataset.autoFill = "false";
    }

    const catSel = modal.querySelector("#editCategory");
    const svcSel = modal.querySelector("#editService");
    catSel.value = category || "";
    await loadServicesByCategory(category || "", svcSel);
    syncEnhancedSelect(svcSel, serviceId || "", serviceName || "");

    const groundDiv = modal.querySelector(".ground-fields");
    const dateInp = modal.querySelector("[name='scheduled_date']");
    const previewTA = modal.querySelector("[name='dispatch_preview']");

    if (category === "ground") {
      if (groundDiv) {
        groundDiv.style.display = "block";
      }
      modal.querySelectorAll(".service-assignee-group").forEach((group) => {
        group.style.display = "";
      });

      if (dateInp) {
        dateInp.value = scheduledDate || "";
      }
      if (previewTA) {
        previewTA.value = dispatchPreview || "";
        if (dispatchPreview) {
          previewTA.dataset.userEdited = "true";
        } else {
          delete previewTA.dataset.userEdited;
        }
      }

      const totalOverrideSection = modal.querySelector(".totalPriceOverride");
      if (totalOverrideSection) {
        totalOverrideSection.style.display = "block";
      }
      modal.querySelector("#editOverrideTotalPrice").value =
        overrideTotal || "";

      const processSection = modal.querySelector(".processCostSection");
      if (processSection) {
        processSection.style.display = "none";
      }
      return;
    }

    if (groundDiv) {
      groundDiv.style.display = "none";
    }
    if (dateInp) {
      dateInp.value = "";
    }
    if (previewTA) {
      previewTA.value = "";
      delete previewTA.dataset.userEdited;
    }

    const ids = (psIds || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const costs = (psCosts || "")
      .split(",")
      .map((s) => parseFloat(String(s || "").trim()));
    const onboardingIds = (psOnboardingIds || "")
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const assigneeMap = parseProcessAssigneeMap(psAssignees);

    const overriddenMap = {
      onboardingIds,
      assigneeMap,
      defaultAssigneeId: assignedEmployeeId || "",
      useDefaultAssigneeFallback: false,
      total: overrideTotal || "",
    };
    ids.forEach((id, index) => {
      overriddenMap[id] = costs[index];
    });

    const data = await loadProcessesForService(
      serviceId,
      modal,
      true,
      overriddenMap,
    );

    if (data?.processes?.length) {
      ids.forEach((id, index) => {
        const rowInp = modal.querySelector(
          `input[name='process_id[]'][value='${id}']`,
        );
        if (!rowInp) {
          return;
        }

        const row = rowInp.closest("tr");
        const costInput = row?.querySelector('input[name="process_cost[]"]');
        const onboardingToggle = row?.querySelector(".onboarding-toggle");
        const hiddenCost = row?.querySelector(".onboarding-hidden-cost");
        const normalizedCost = Number.isFinite(costs[index]) ? costs[index] : 0;

        if (!costInput) {
          return;
        }

        if (onboardingToggle?.checked) {
          if (!costInput.dataset.previousCost) {
            costInput.dataset.previousCost = String(normalizedCost);
          }
          costInput.value = 0;
          if (hiddenCost) {
            hiddenCost.value = "0";
          }
        } else {
          costInput.value = normalizedCost;
        }
      });

      const processSection = modal.querySelector(".processCostSection");
      if (processSection) {
        processSection.style.display = "block";
      }
      const totalOverrideSection = modal.querySelector(".totalPriceOverride");
      if (totalOverrideSection) {
        totalOverrideSection.style.display = "none";
      }
    } else {
      const totalOverrideSection = modal.querySelector(".totalPriceOverride");
      if (totalOverrideSection) {
        totalOverrideSection.style.display = "block";
      }
      modal.querySelector("#editOverrideTotalPrice").value =
        overrideTotal || "";
    }

    recalculateTotal(modal);

    modal.querySelectorAll(".cost-input").forEach((inp) => {
      inp.addEventListener("input", () => recalculateTotal(modal));
    });
  });
}
