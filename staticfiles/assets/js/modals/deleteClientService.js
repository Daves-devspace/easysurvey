export function initDeleteClientServiceModal() {
    const modal = document.getElementById("deleteClientServiceModal");
    modal?.addEventListener("show.bs.modal", e => {
        const btn = e.relatedTarget;
        modal.querySelector("#deleteClientServiceId").value = btn.dataset.csid;
        modal.querySelector("#deleteClientServiceInfo").textContent = btn.dataset.csinfo;
    });
}
