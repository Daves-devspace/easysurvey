function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function getEmployeeOptions(modalEl) {
  const serviceAssigneeSelect = modalEl.querySelector(
    "[name='assigned_employee']",
  );
  if (!serviceAssigneeSelect) {
    return [{ value: "", label: "Search or select assignee" }];
  }

  const options = Array.from(serviceAssigneeSelect.options || []).map(
    (option) => ({
      value: String(option.value ?? "").trim(),
      label: option.textContent?.trim() || "",
    }),
  );

  if (!options.length || options[0].value !== "") {
    options.unshift({ value: "", label: "Search or select assignee" });
  }

  return options;
}

function normalizeAssigneeMap(rawMap) {
  if (!rawMap || typeof rawMap !== "object") {
    return {};
  }

  const normalized = {};
  Object.entries(rawMap).forEach(([processId, assigneeIds]) => {
    const key = String(processId);
    if (!Array.isArray(assigneeIds)) {
      normalized[key] = [];
      return;
    }

    const seen = new Set();
    const values = [];
    assigneeIds.forEach((value) => {
      const normalizedValue = String(value ?? "").trim();
      if (!normalizedValue || seen.has(normalizedValue)) {
        return;
      }
      seen.add(normalizedValue);
      values.push(normalizedValue);
    });

    normalized[key] = values;
  });

  return normalized;
}

function renderAssigneeSelectRow(
  processId,
  employeeOptions,
  selectedEmployeeId = "",
) {
  const normalizedSelected = String(selectedEmployeeId ?? "").trim();
  const optionsHtml = employeeOptions
    .map((option) => {
      const value = String(option.value ?? "").trim();
      const selected = value === normalizedSelected ? "selected" : "";
      return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(option.label)}</option>`;
    })
    .join("");

  return `
    <div class="mb-1 process-assignee-row">
      <div class="process-assignee-select-wrap">
        <select class="form-select searchable-select process-assignee-select"
                name="process_assignees_${processId}[]"
                data-search-placeholder="Search or select assignee">
          ${optionsHtml}
        </select>
      </div>
      <div class="btn-group btn-group-sm process-assignee-actions" role="group" aria-label="Assignee actions">
        <button type="button" class="btn btn-outline-secondary add-process-assignee" data-process-id="${processId}" title="Add assignee" aria-label="Add assignee">+</button>
        <button type="button" class="btn btn-outline-danger remove-process-assignee" title="Remove assignee" aria-label="Remove assignee">&times;</button>
      </div>
    </div>
  `;
}

function toggleServiceAssignmentFields(modalEl, hasProcesses) {
  modalEl.querySelectorAll(".service-assignee-group").forEach((group) => {
    group.style.display = hasProcesses ? "none" : "";
  });
}

function notifyProcessAssigneeRender(modalEl) {
  modalEl.dispatchEvent(new CustomEvent("process-assignees-rendered"));
}

function initProcessAssigneeInteractions(procBody, modalEl, employeeOptions) {
  if (!procBody) {
    return;
  }

  procBody.onclick = (event) => {
    const addBtn = event.target.closest(".add-process-assignee");
    if (addBtn) {
      const processId = String(addBtn.dataset.processId || "").trim();
      if (!processId) {
        return;
      }

      const list = procBody.querySelector(
        `.process-assignee-list[data-process-id="${processId}"]`,
      );
      if (!list) {
        return;
      }

      list.insertAdjacentHTML(
        "beforeend",
        renderAssigneeSelectRow(processId, employeeOptions, ""),
      );
      notifyProcessAssigneeRender(modalEl);
      return;
    }

    const removeBtn = event.target.closest(".remove-process-assignee");
    if (!removeBtn) {
      return;
    }

    const row = removeBtn.closest(".process-assignee-row");
    const list = row?.closest(".process-assignee-list");
    if (!row || !list) {
      return;
    }

    const rows = list.querySelectorAll(".process-assignee-row");
    if (rows.length <= 1) {
      const select = row.querySelector("select");
      if (select) {
        select.value = "";
      }
      notifyProcessAssigneeRender(modalEl);
      return;
    }

    row.remove();
    notifyProcessAssigneeRender(modalEl);
  };
}

export async function loadServicesByCategory(category, selectEl) {
  selectEl.innerHTML = "<option>Loading…</option>";
  try {
    const res = await fetch(`/services/by-category/?category=${category}`);
    const { services } = await res.json();
    selectEl.innerHTML =
      `<option value="">Select Service</option>` +
      services
        .map((s) => `<option value="${s.id}">${s.name}</option>`)
        .join("");
  } catch (error) {
    console.error("Error loading services:", error);
    selectEl.innerHTML = `<option value="">Error loading services</option>`;
  }
}

export async function loadProcessesForService(
  serviceId,
  modalEl,
  skipListeners = false,
  overridden = null,
) {
  const procSec = modalEl.querySelector(".processCostSection");
  const procBody = modalEl.querySelector(".processTableBody");
  const totalEls = modalEl.querySelectorAll(".totalCost");
  const overSec = modalEl.querySelector(".totalPriceOverride");
  const overInp = modalEl.querySelector(".overrideTotalPrice");
  const durationInp = modalEl.querySelector("[name='expected_duration_days']");

  if (!serviceId) {
    if (procBody) {
      procBody.innerHTML = "";
    }
    if (procSec) {
      procSec.style.display = "none";
    }
    if (overSec) {
      overSec.style.display = "none";
    }
    toggleServiceAssignmentFields(modalEl, false);
    totalEls.forEach((el) => (el.textContent = "0.00"));
    notifyProcessAssigneeRender(modalEl);
    return { processes: [] };
  }

  try {
    const query = new URLSearchParams();
    const clientId = String(
      modalEl.querySelector("input[name='client']")?.value || "",
    ).trim();
    const editingClientServiceId = String(
      modalEl.querySelector("input[name='client_service_id']")?.value || "",
    ).trim();
    const globalFallbackFlag = String(
      modalEl.dataset.useGlobalPrefillFallback || "",
    )
      .trim()
      .toLowerCase();

    if (clientId) {
      query.set("client_id", clientId);
    }
    if (editingClientServiceId) {
      query.set("exclude_client_service_id", editingClientServiceId);
    }
    if (["1", "true", "yes", "on"].includes(globalFallbackFlag)) {
      query.set("global_fallback", "1");
    }

    const endpoint = query.toString()
      ? `/get_service_processes/${serviceId}/?${query.toString()}`
      : `/get_service_processes/${serviceId}/`;
    const res = await fetch(endpoint);
    const json = await res.json();

    if (procBody) {
      procBody.innerHTML = "";
    }
    let total = 0;

    const employeeOptions = getEmployeeOptions(modalEl);
    const hasOverrideAssigneeMap =
      Boolean(overridden) &&
      Object.prototype.hasOwnProperty.call(overridden, "assigneeMap");
    const assigneeMap = hasOverrideAssigneeMap
      ? normalizeAssigneeMap(overridden?.assigneeMap)
      : normalizeAssigneeMap(json?.suggested_assignee_map);
    const useDefaultAssigneeFallback = Boolean(
      overridden?.useDefaultAssigneeFallback ?? !hasOverrideAssigneeMap,
    );
    const defaultAssigneeId = String(
      overridden?.defaultAssigneeId ??
        json?.suggested_default_assignee_id ??
        "",
    ).trim();

    if (json.processes.length) {
      overSec.style.display = "none";
      procSec.style.display = "block";
      toggleServiceAssignmentFields(modalEl, true);

      const onboardingSet = new Set(
        (overridden?.onboardingIds || []).map((item) => String(item)),
      );

      json.processes.forEach((p) => {
        const overriddenCost = overridden?.[String(p.id)];
        const cost =
          overriddenCost !== undefined ? overriddenCost : p.default_cost;
        const isOnboarded = onboardingSet.has(String(p.id));
        const effectiveCost = isOnboarded ? 0 : cost;
        total += effectiveCost;

        const processKey = String(p.id);
        const hasConfiguredAssignees = Object.prototype.hasOwnProperty.call(
          assigneeMap,
          processKey,
        );
        const configuredAssignees = hasConfiguredAssignees
          ? assigneeMap[processKey]
          : [];
        const initialAssignees = hasConfiguredAssignees
          ? configuredAssignees.length
            ? configuredAssignees
            : [""]
          : useDefaultAssigneeFallback && defaultAssigneeId
            ? [defaultAssigneeId]
            : [""];

        const assigneeRowsHtml = initialAssignees
          .map((employeeId) =>
            renderAssigneeSelectRow(p.id, employeeOptions, employeeId),
          )
          .join("");

        procBody.innerHTML += `
                    <tr>
                        <td>${p.name}
                            <input type="hidden" name="process_id[]" value="${p.id}">
                        </td>
                        <td>
                            <input type="number" name="process_cost[]" class="form-control cost-input"
                                step="0.01" value="${effectiveCost}" ${isOnboarded ? "disabled" : ""}
                                ${isOnboarded ? `data-previous-cost="${cost}"` : ""}>
                            ${
                              isOnboarded
                                ? `<input type="hidden" name="process_cost[]" class="onboarding-hidden-cost" value="0">`
                                : ""
                            }
                        </td>
                        <td>
                          <div class="form-check d-flex justify-content-center">
                            <input type="checkbox" name="completed_at_onboarding[]" class="form-check-input onboarding-toggle"
                              value="${p.id}" ${isOnboarded ? "checked" : ""}>
                          </div>
                        </td>
                        <td>
                          <div class="process-assignee-list" data-process-id="${p.id}">
                            ${assigneeRowsHtml}
                          </div>
                        </td>
                    </tr>`;
      });

      initProcessAssigneeInteractions(procBody, modalEl, employeeOptions);
      notifyProcessAssigneeRender(modalEl);
    } else {
      procSec.style.display = "none";
      overSec.style.display = "block";
      toggleServiceAssignmentFields(modalEl, false);
      overInp.value = overridden?.total || json.total_price;
      total = overridden?.total || json.total_price;
      notifyProcessAssigneeRender(modalEl);
    }

    procBody.querySelectorAll(".onboarding-toggle").forEach((checkbox) => {
      const syncOnboardingRow = () => {
        const row = checkbox.closest("tr");
        const costInput = row?.querySelector(".cost-input");
        if (!row || !costInput) return;

        let hiddenCost = row.querySelector(".onboarding-hidden-cost");

        if (checkbox.checked) {
          if (!costInput.dataset.previousCost) {
            costInput.dataset.previousCost = costInput.value || "0";
          }
          if (!costInput.disabled) {
            costInput.dataset.previousCost = costInput.value || "0";
          }

          costInput.value = "0";
          costInput.disabled = true;

          if (!hiddenCost) {
            hiddenCost = document.createElement("input");
            hiddenCost.type = "hidden";
            hiddenCost.name = "process_cost[]";
            hiddenCost.className = "onboarding-hidden-cost";
            costInput.insertAdjacentElement("afterend", hiddenCost);
          }
          hiddenCost.value = "0";
        } else {
          costInput.disabled = false;
          const previousCost = costInput.dataset.previousCost;
          if (previousCost !== undefined) {
            costInput.value = previousCost;
            delete costInput.dataset.previousCost;
          }
          if (hiddenCost) hiddenCost.remove();
        }
      };

      syncOnboardingRow();

      checkbox.addEventListener("change", () => {
        syncOnboardingRow();
        recalculateTotal(modalEl);
      });
    });

    procBody.querySelectorAll(".cost-input").forEach((costInput) => {
      costInput.addEventListener("input", () => {
        const row = costInput.closest("tr");
        const hiddenCost = row?.querySelector(".onboarding-hidden-cost");
        if (hiddenCost) {
          hiddenCost.value = costInput.value || "0";
        }
      });
    });

    if (
      durationInp &&
      json.expected_duration_days &&
      (!durationInp.value || durationInp.dataset.autoFill !== "false")
    ) {
      durationInp.value = json.expected_duration_days;
      durationInp.dataset.autoFill = "true";
    }

    totalEls.forEach((el) => (el.textContent = total.toFixed(2)));

    if (!skipListeners) {
      modalEl.querySelectorAll(".cost-input").forEach((inp) => {
        inp.addEventListener("input", () => recalculateTotal(modalEl));
      });
    }

    return json;
  } catch (error) {
    console.error("Error loading processes:", error);
    toggleServiceAssignmentFields(modalEl, false);
    notifyProcessAssigneeRender(modalEl);
  }
}

export function recalculateTotal(modal) {
  let sum = 0;
  modal.querySelectorAll(".cost-input").forEach((c) => {
    sum += parseFloat(c.value) || 0;
  });
  modal.querySelectorAll(".totalCost").forEach((el) => {
    el.textContent = sum.toFixed(2);
  });
}
