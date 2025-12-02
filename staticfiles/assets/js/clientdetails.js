import { initServiceModal } from "./modals/serviceModal.js";
import { bindEditService } from "./modals/editServiceModal.js";
import { initDeleteClientServiceModal } from "./modals/deleteClientService.js";
import { initSubServiceModals } from "./modals/subServiceModals.js";

document.addEventListener("DOMContentLoaded", () => {
    initServiceModal("#addClientServiceModal");
    initServiceModal("#editClientServiceModal");
    document.querySelectorAll(".edit-client-service-btn").forEach(bindEditService);

    initDeleteClientServiceModal();
    initSubServiceModals();
});
