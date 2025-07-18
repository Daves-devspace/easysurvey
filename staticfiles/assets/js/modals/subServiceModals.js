export function initSubServiceModals() {
    const editModal = document.getElementById("editSubServiceModal");
    editModal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        editModal.querySelector("#editSubServiceId").value = btn.dataset.clientSubserviceId;
        editModal.querySelector("#editModalSubServiceName").value = btn.dataset.subserviceName;
        editModal.querySelector("#editModalDepartment").value = btn.dataset.department;
        editModal.querySelector("#editModalOverriddenPrice").value = btn.dataset.overriddenPrice;
    });

    const deleteModal = document.getElementById("deleteSubServiceModal");
    deleteModal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        deleteModal.querySelector("#deleteSubServiceId").value = btn.dataset.clientSubserviceId;
        deleteModal.querySelector("#deleteSubServiceInfo").textContent = btn.dataset.csinfo;
    });

    const addModal = document.getElementById("addSubServiceModal");
    addModal?.addEventListener("show.bs.modal", e => {
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
}
