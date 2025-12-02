document.addEventListener("DOMContentLoaded", () => {
    // CLIENT‑SERVICE MODALS

    async function loadServicesByCategory(category, selectEl) {
        selectEl.innerHTML = "<option>Loading…</option>";
        try {
            const res = await fetch(`/services/by-category/?category=${category}`);
            const {services} = await res.json();
            selectEl.innerHTML = `<option value="">Select Service</option>` +
                services.map(s => `<option value="${s.id}">${s.name}</option>`).join("");
        } catch (error) {
            console.error("Error loading services:", error);
            selectEl.innerHTML = `<option value="">Error loading services</option>`;
        }
    }

    async function loadProcessesForService(serviceId, modalEl, skipListeners = false, overridden = null) {
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
                    inp.addEventListener("input", () => {
                        recalculateTotal(modalEl);
                    });
                });
            }

            return json;
        } catch (error) {
            console.error("Error loading processes:", error);
        }
    }

    function initServiceModal(modalSelector) {
        const modalEl = document.querySelector(modalSelector);
        if (!modalEl) return;

        const catSel = modalEl.querySelector("[name='category']");
        const svcSel = modalEl.querySelector("[name='service']");

        // Hide groundFields helper
        function toggleGroundFields(isGround) {
            const gf = modalEl.querySelector("#groundFields");
            if (!gf) return;
            gf.style.display = isGround ? "block" : "none";
        }

        // On category change: repopulate services & hide everything
        catSel?.addEventListener("change", () => {
            loadServicesByCategory(catSel.value, svcSel);
            modalEl.querySelector(".processCostSection").style.display = "none";
            modalEl.querySelector(".totalPriceOverride").style.display = "none";
            toggleGroundFields(false);
        });

        // On service change: show groundFields if category===ground, then load processes
        svcSel?.addEventListener("change", () => {
            const categoryVal = catSel.value;
            console.log("Selected category:", categoryVal);  // Add this
            const isGround = catSel.value === 'ground';
            toggleGroundFields(isGround);
            loadProcessesForService(svcSel.value, modalEl);
        });
    }


    // function initServiceModal(selector) {
    //     const modalEl = document.querySelector(selector);
    //     if (!modalEl) return;
    //
    //     const catSel = modalEl.querySelector("[name='category']");
    //     const svcSel = modalEl.querySelector("[name='service']");
    //
    //     catSel?.addEventListener("change", () => loadServicesByCategory(catSel.value, svcSel));
    //     svcSel?.addEventListener("change", () => loadProcessesForService(svcSel.value, modalEl));
    // }

    function bindEditService(btn) {
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
                inp.addEventListener("input", () => {
                    recalculateTotal(modal);
                });
            });

            const bsModal = new bootstrap.Modal(modal);
            bsModal.show();
        });
    }

    function recalculateTotal(modal) {
        let sum = 0;
        modal.querySelectorAll(".cost-input").forEach(c => {
            sum += parseFloat(c.value) || 0;
        });
        modal.querySelectorAll(".totalCost").forEach(el => {
            el.textContent = sum.toFixed(2);
        });
    }

    // Initialize modals
    initServiceModal("#addClientServiceModal");
    initServiceModal("#editClientServiceModal");
    document.querySelectorAll(".edit-client-service-btn")
        .forEach(bindEditService);

    // DELETE CLIENT‑SERVICE MODAL
    const deleteCSModal = document.getElementById("deleteClientServiceModal");
    deleteCSModal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        deleteCSModal.querySelector("#deleteClientServiceId").value = btn.dataset.csid;
        deleteCSModal.querySelector("#deleteClientServiceInfo").textContent = btn.dataset.csinfo;
    });

    // EDIT SUBSERVICE MODAL
    const editSSModal = document.getElementById("editSubServiceModal");
    editSSModal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        editSSModal.querySelector("#editSubServiceId").value = btn.dataset.clientSubserviceId;
        editSSModal.querySelector("#editModalSubServiceName").value = btn.dataset.subserviceName;
        editSSModal.querySelector("#editModalDepartment").value = btn.dataset.department;
        editSSModal.querySelector("#editModalOverriddenPrice").value = btn.dataset.overriddenPrice;
    });

    // DELETE SUBSERVICE MODAL
    const deleteSSModal = document.getElementById("deleteSubServiceModal");
    deleteSSModal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        deleteSSModal.querySelector("#deleteSubServiceId").value = btn.dataset.clientSubserviceId;
        deleteSSModal.querySelector("#deleteSubServiceInfo").textContent = btn.dataset.csinfo;
    });

    // ADD SUBSERVICE MODAL
    const addSSModal = document.getElementById("addSubServiceModal");
    addSSModal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        document.getElementById("modal_service_id").value = btn.dataset.serviceId;
        document.getElementById("modal_service_name").textContent = btn.dataset.serviceName;
        document.getElementById("modal_sub_service").selectedIndex = 0;
        document.getElementById("modal_department").value = '';
        document.getElementById("modal_overridden_price").value = '';
    });

    document.getElementById("modal_sub_service")?.addEventListener("change", function () {
        const opt = this.options[this.selectedIndex];
        document.getElementById("modal_overridden_price").value = opt.dataset.price || '';
        document.getElementById("modal_department").value = opt.dataset.dept || '';
    });
});


// // static/js/client_details_payments.js
//
// document.addEventListener("DOMContentLoaded", () => {
//     //
//     // CLIENT‑SERVICE MODALS
//
//     async function loadServicesByCategory(category, selectEl) {
//         selectEl.innerHTML = "<option>Loading…</option>";
//         const res = await fetch(`/services/by-category/?category=${category}`);
//         const {services} = await res.json();
//         selectEl.innerHTML = `<option value="">Select Service</option>` +
//             services.map(s => `<option value="${s.id}">${s.name}</option>`).join("");
//     }
//
//     async function loadProcessesForService(serviceId, modalEl, skipListeners = false, overridden = null) {
//         const procSec = modalEl.querySelector(".processCostSection");
//         const procBody = modalEl.querySelector(".processTableBody");
//         const totalEls = modalEl.querySelectorAll(".totalCost");
//         const overSec = modalEl.querySelector(".totalPriceOverride");
//         const overInp = modalEl.querySelector(".overrideTotalPrice");
//
//         const res = await fetch(`/get_service_processes/${serviceId}/`);
//         const json = await res.json();
//
//         procBody.innerHTML = "";
//         let total = 0;
//
//         if (json.processes.length) {
//             overSec.style.display = "none";
//             procSec.style.display = "block";
//
//             json.processes.forEach(p => {
//                 const overriddenCost = overridden?.[String(p.id)]; // Ensure the process id is a string
//                 const cost = overriddenCost !== undefined ? overriddenCost : p.default_cost;
//                 total += cost;
//
//                 procBody.innerHTML += `
//             <tr>
//                 <td>${p.name}
//                     <input type="hidden" name="process_id[]" value="${p.id}">
//                 </td>
//                 <td>
//                     <input type="number" name="process_cost[]" class="form-control cost-input"
//                         step="0.01" value="${cost}">
//                 </td>
//             </tr>`;
//             });
//         } else {
//             procSec.style.display = "none";
//             overSec.style.display = "block";
//             overInp.value = overridden?.total || json.total_price;
//             total = overridden?.total || json.total_price;
//         }
//
//         totalEls.forEach(el => el.textContent = total.toFixed(2));
//
//         if (!skipListeners) {
//             modalEl.querySelectorAll(".cost-input").forEach(inp => {
//                 inp.addEventListener("input", () => {
//                     let sum = 0;
//                     modalEl.querySelectorAll(".cost-input")
//                         .forEach(c => sum += parseFloat(c.value) || 0);
//                     totalEls.forEach(el => el.textContent = sum.toFixed(2));
//                 });
//             });
//         }
//
//         return json;
//     }
//
//     function initServiceModal(selector) {
//         const modalEl = document.querySelector(selector);
//         if (!modalEl) return;
//
//         const catSel = modalEl.querySelector("[name='category']");
//         const svcSel = modalEl.querySelector("[name='service']");
//
//         catSel?.addEventListener("change", () => loadServicesByCategory(catSel.value, svcSel));
//         svcSel?.addEventListener("change", () => loadProcessesForService(svcSel.value, modalEl));
//     }
//
//     function bindEditService(btn) {
//         btn.addEventListener("click", async () => {
//             const modal = document.getElementById("editClientServiceModal");
//             const {
//                 serviceId, category, clientServiceId, landDescription,
//                 overrideTotal, psIds, psCosts
//             } = btn.dataset;
//
//             // hidden fields
//             modal.querySelector("#editClientServiceId").value = clientServiceId;
//             modal.querySelector("#editLandDescription").value = landDescription || "";
//
//             // prefill client info from data attrs
//             modal.querySelector("#editClientName").value = btn.dataset.clientName;
//             modal.querySelector("#editClientPhone").value = btn.dataset.clientPhone;
//
//             // category & service
//             const catSel = modal.querySelector("#editCategory");
//             const svcSel = modal.querySelector("#editService");
//             catSel.value = category;
//             await loadServicesByCategory(category, svcSel);
//             svcSel.value = serviceId;
//
//             // Build an overridden cost map (e.g., { "1": 1200, "2": 3000 })
//             const ids = (psIds || "").split(",").map(s => s.trim());
//             const costs = (psCosts || "").split(",").map(s => parseFloat(s.trim()));
//             const overriddenMap = {};
//             ids.forEach((id, i) => overriddenMap[id] = costs[i]);
//
//             const data = await loadProcessesForService(serviceId, modal, true, overriddenMap);
//
//             // override costs if there are processes
//             if (data.processes.length) {
//                 ids.forEach((id, i) => {
//                     const rowInp = modal.querySelector(`input[name="process_id[]"][value="${id}"]`);
//                     if (rowInp) {
//                         const costInput = rowInp.closest("tr").querySelector('input[name="process_cost[]"]');
//                         costInput.value = costs[i] || 0;  // Set overridden cost
//                     }
//                 });
//
//                 // Show process cost section
//                 modal.querySelector(".processCostSection").style.display = "block";
//             } else {
//                 // If no processes, just set the override total price
//                 modal.querySelector("#editOverrideTotalPrice").value = overrideTotal;
//             }
//
//             // Recalculate total cost immediately based on overridden costs
//             recalculateTotal(modal);
//
//             // Re-attach cost listeners
//             modal.querySelectorAll(".cost-input").forEach(inp => {
//                 inp.addEventListener("input", () => {
//                     recalculateTotal(modal);
//                 });
//             });
//
//             // Show modal
//             const bsModal = new bootstrap.Modal(modal);
//             bsModal.show();
//         });
//     }
//
//     function recalculateTotal(modal) {
//         let sum = 0;
//         modal.querySelectorAll(".cost-input").forEach(c => {
//             sum += parseFloat(c.value) || 0;
//         });
//         modal.querySelectorAll(".totalCost").forEach(el => {
//             el.textContent = sum.toFixed(2);
//         });
//     }
//
// // init both add & edit service modals
//     initServiceModal("#addClientServiceModal");
//     initServiceModal("#editClientServiceModal");
//     document.querySelectorAll(".edit-client-service-btn")
//         .forEach(bindEditService);
//
//
//     //
//     // 2) DELETE CLIENT‑SERVICE MODAL
//     //
//     const deleteCSModal = document.getElementById("deleteClientServiceModal");
//     deleteCSModal?.addEventListener("show.bs.modal", e => {
//         const btn = e.relatedTarget;
//         deleteCSModal.querySelector("#deleteClientServiceId").value = btn.dataset.csid;
//         deleteCSModal.querySelector("#deleteClientServiceInfo").textContent = btn.dataset.csinfo;
//     });
//
//     //
//     // 3) EDIT SUBSERVICE MODAL
//     //
//     const editSSModal = document.getElementById("editSubServiceModal");
//     editSSModal?.addEventListener("show.bs.modal", e => {
//         const btn = e.relatedTarget;
//         editSSModal.querySelector("#editSubServiceId").value = btn.dataset.clientSubserviceId;
//         editSSModal.querySelector("#editModalSubServiceName").value = btn.dataset.subserviceName;
//         editSSModal.querySelector("#editModalDepartment").value = btn.dataset.department;
//         editSSModal.querySelector("#editModalOverriddenPrice").value = btn.dataset.overriddenPrice;
//     });
//
//     //
//     // 4) DELETE SUBSERVICE MODAL
//     //
//     const deleteSSModal = document.getElementById("deleteSubServiceModal");
//     deleteSSModal?.addEventListener("show.bs.modal", e => {
//         const btn = e.relatedTarget;
//         deleteSSModal.querySelector("#deleteSubServiceId").value = btn.dataset.clientSubserviceId;
//         deleteSSModal.querySelector("#deleteSubServiceInfo").textContent = btn.dataset.csinfo;
//     });
//
//     //
//     // 5) ADD SUBSERVICE MODAL
//     //
//     const addSSModal = document.getElementById("addSubServiceModal");
//     addSSModal?.addEventListener("show.bs.modal", e => {
//         const btn = e.relatedTarget;
//         document.getElementById("modal_service_id").value = btn.dataset.serviceId;
//         document.getElementById("modal_service_name").textContent = btn.dataset.serviceName;
//         document.getElementById("modal_sub_service").selectedIndex = 0;
//         document.getElementById("modal_department").value = '';
//         document.getElementById("modal_overridden_price").value = '';
//     });
//
//     document.getElementById("modal_sub_service")?.addEventListener("change", function () {
//         const opt = this.options[this.selectedIndex];
//         document.getElementById("modal_overridden_price").value = opt.dataset.price || '';
//         document.getElementById("modal_department").value = opt.dataset.dept || '';
//     });
// });
