import { loadServicesByCategory, loadProcessesForService, recalculateTotal } from "../utils/serviceUtils.js";

export function bindEditService(btn) {
    btn.addEventListener("click", async () => {
        const modal = document.getElementById("editClientServiceModal");
        const {
            serviceId, category, clientServiceId, landDescription,
            overrideTotal, psIds, psCosts
        } = btn.dataset;

        modal.querySelector("#editClientServiceId").value = clientServiceId;
        modal.querySelector("#editLandDescription").value = landDescription || "";
        modal.querySelector("#editClientName").value = btn.dataset.clientName;
        modal.querySelector("#editClientPhone").value = btn.dataset.clientPhone;

        const catSel = modal.querySelector("#editCategory");
        const svcSel = modal.querySelector("#editService");
        catSel.value = category;
        await loadServicesByCategory(category, svcSel);
        svcSel.value = serviceId;

        const ids = (psIds || "").split(",").map(s => s.trim());
        const costs = (psCosts || "").split(",").map(s => parseFloat(s.trim()));
        const overriddenMap = {};
        ids.forEach((id, i) => overriddenMap[id] = costs[i]);

        const data = await loadProcessesForService(serviceId, modal, true, overriddenMap);

        if (data.processes.length) {
            ids.forEach((id, i) => {
                const rowInp = modal.querySelector(`input[name="process_id[]"][value="${id}"]`);
                if (rowInp) {
                    const costInput = rowInp.closest("tr").querySelector('input[name="process_cost[]"]');
                    costInput.value = costs[i] || 0;
                }
            });
            modal.querySelector(".processCostSection").style.display = "block";
        } else {
            modal.querySelector("#editOverrideTotalPrice").value = overrideTotal;
        }

        recalculateTotal(modal);

        modal.querySelectorAll(".cost-input").forEach(inp => {
            inp.addEventListener("input", () => recalculateTotal(modal));
        });

        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();
    });
}
