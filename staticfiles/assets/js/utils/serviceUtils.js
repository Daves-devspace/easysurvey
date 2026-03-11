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

  try {
    const res = await fetch(`/get_service_processes/${serviceId}/`);
    const json = await res.json();

    procBody.innerHTML = "";
    let total = 0;

    if (json.processes.length) {
      overSec.style.display = "none";
      procSec.style.display = "block";

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
                    </tr>`;
      });
    } else {
      procSec.style.display = "none";
      overSec.style.display = "block";
      overInp.value = overridden?.total || json.total_price;
      total = overridden?.total || json.total_price;
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
