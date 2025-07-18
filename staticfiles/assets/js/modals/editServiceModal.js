import { loadServicesByCategory, loadProcessesForService, recalculateTotal } from "../utils/serviceUtils.js";

export function bindEditService(btn) {
  btn.addEventListener("click", async () => {
    // 1️⃣ Grab the Edit modal container
    const modal = document.getElementById("editClientServiceModal");

    // 2️⃣ Destructure all needed data-attributes from the clicked button, including ground-service fields
    const {
      serviceId,
      category,
      clientServiceId,
      landDescription,
      overrideTotal,
      psIds,
      psCosts,
      scheduledDate,       // newly added for ground services
      dispatchPreview      // newly added for ground services
    } = btn.dataset;

    // 3️⃣ Populate hidden & read-only fields
    modal.querySelector("#editClientServiceId").value = clientServiceId;
    modal.querySelector("#editLandDescription").value = landDescription || "";
    modal.querySelector("#editClientName").value = btn.dataset.clientName;
    modal.querySelector("#editClientPhone").value = btn.dataset.clientPhone;

    // 4️⃣ Category & Service selects: set category, load services, then set the selected service
    const catSel = modal.querySelector("#editCategory");
    const svcSel = modal.querySelector("#editService");
    catSel.value = category;
    await loadServicesByCategory(category, svcSel); // load services for this category
    svcSel.value = serviceId;

    // 5️⃣ Ground‑service branch (overrides process logic for ground)
    const groundDiv = modal.querySelector(".ground-fields");
    const dateInp   = modal.querySelector("[name='scheduled_date']");
    const previewTA = modal.querySelector("[name='dispatch_preview']");

    if (category === "ground") {
      // Show & populate ground-specific fields
      groundDiv.style.display = "block";
      if (scheduledDate)   dateInp.value   = scheduledDate;
      if (dispatchPreview) previewTA.value = dispatchPreview;

      // Always allow override total on ground services
      const totSec = modal.querySelector(".totalPriceOverride");
      totSec.style.display = "block";
      modal.querySelector("#editOverrideTotalPrice").value = overrideTotal || "";

      // Hide process section entirely
      modal.querySelector(".processCostSection").style.display = "none";

      // 6️⃣ Skip to rendering modal
      new bootstrap.Modal(modal).show();
      return; // bypass process logic
    } else {
      // Non-ground: ensure ground fields hidden
      groundDiv.style.display = "none";
    }

    // 7️⃣ Process override logic (for TITLE and other non-ground services)
    const ids   = (psIds   || "").split(",").map(s => s.trim());
    const costs = (psCosts || "").split(",").map(s => parseFloat(s.trim()));
    const overriddenMap = {};
    ids.forEach((id, i) => overriddenMap[id] = costs[i]);

    const data = await loadProcessesForService(serviceId, modal, true, overriddenMap);

    if (data.processes.length) {
      // Service has processes: show table and prefill costs
      ids.forEach((id, i) => {
        const rowInp = modal.querySelector(`input[name='process_id[]'][value='${id}']`);
        if (rowInp) {
          const costInput = rowInp.closest("tr").querySelector('input[name="process_cost[]"]');
          costInput.value = costs[i] || 0;
        }
      });
      modal.querySelector(".processCostSection").style.display = "block";
    } else {
      // No processes: fallback to override total for non-ground
      const totSec = modal.querySelector(".totalPriceOverride");
      totSec.style.display = "block";
      modal.querySelector("#editOverrideTotalPrice").value = overrideTotal || "";
    }

    // 8️⃣ Calculate the total cost
    recalculateTotal(modal);

    // 9️⃣ Re-calc on each cost input change
    modal.querySelectorAll(".cost-input").forEach(inp => {
      inp.addEventListener("input", () => recalculateTotal(modal));
    });

    // 🔟 Show the modal
    new bootstrap.Modal(modal).show();
  });
}

/*
  🔄 Key Updates:
  - Ground branch (step 5):
    • Always displays both ground-fields and override-total sections
    • Populates scheduledDate & dispatchPreview
    • Skips process override logic via early return
  - Non-ground:
    • Hides ground-fields
    • Runs process override logic (step 7)
    • Falls back to override-total if no processes (step 7)
  - Ensures that override-total shows for both ground (step 5) and non-ground no-process (step 7).
  - Maintains original flow for Title services with processes.
*/
