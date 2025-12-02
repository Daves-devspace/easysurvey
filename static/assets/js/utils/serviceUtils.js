export async function loadServicesByCategory(category, selectEl) {
    selectEl.innerHTML = "<option>Loading…</option>";
    try {
        const res = await fetch(`/services/by-category/?category=${category}`);
        const { services } = await res.json();
        selectEl.innerHTML = `<option value="">Select Service</option>` +
            services.map(s => `<option value="${s.id}">${s.name}</option>`).join("");
    } catch (error) {
        console.error("Error loading services:", error);
        selectEl.innerHTML = `<option value="">Error loading services</option>`;
    }
}

export async function loadProcessesForService(serviceId, modalEl, skipListeners = false, overridden = null) {
    const procSec = modalEl.querySelector(".processCostSection");
    const procBody = modalEl.querySelector(".processTableBody");
    const totalEls = modalEl.querySelectorAll(".totalCost");
    const overSec = modalEl.querySelector(".totalPriceOverride");
    const overInp = modalEl.querySelector(".overrideTotalPrice");

    try {
        const res = await fetch(`/get_service_processes/${serviceId}/`);
        const json = await res.json();

        procBody.innerHTML = "";
        let total = 0;

        if (json.processes.length) {
            overSec.style.display = "none";
            procSec.style.display = "block";

            json.processes.forEach(p => {
                const overriddenCost = overridden?.[String(p.id)];
                const cost = overriddenCost !== undefined ? overriddenCost : p.default_cost;
                total += cost;

                procBody.innerHTML += `
                    <tr>
                        <td>${p.name}
                            <input type="hidden" name="process_id[]" value="${p.id}">
                        </td>
                        <td>
                            <input type="number" name="process_cost[]" class="form-control cost-input"
                                step="0.01" value="${cost}">
                        </td>
                    </tr>`;
            });
        } else {
            procSec.style.display = "none";
            overSec.style.display = "block";
            overInp.value = overridden?.total || json.total_price;
            total = overridden?.total || json.total_price;
        }

        totalEls.forEach(el => el.textContent = total.toFixed(2));

        if (!skipListeners) {
            modalEl.querySelectorAll(".cost-input").forEach(inp => {
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
    modal.querySelectorAll(".cost-input").forEach(c => {
        sum += parseFloat(c.value) || 0;
    });
    modal.querySelectorAll(".totalCost").forEach(el => {
        el.textContent = sum.toFixed(2);
    });
}
