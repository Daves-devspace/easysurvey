import {
  loadServicesByCategory,
  loadProcessesForService,
  recalculateTotal,
} from "../utils/serviceUtils.js";

/**
 * modalSelector: CSS selector for your modal, e.g. "#addClientServiceModal"
 * clientName: you can pass this in by rendering it on the modal container as
 *             a data attribute: <div id="addClientServiceModal" data-client-name="{{ client.first_name }}">
 */
export function initServiceModal(modalSelector) {
  const modalEl = document.querySelector(modalSelector);
  if (!modalEl) return;

  function getSearchableSelects() {
    return Array.from(
      modalEl.querySelectorAll(
        "select.searchable-select, select[name='service'], select[name='assigned_employee']",
      ),
    ).filter((selectEl, index, all) => all.indexOf(selectEl) === index);
  }

  function enhanceSearchableSelect(selectEl) {
    if (
      !selectEl ||
      typeof window.$ === "undefined" ||
      typeof window.$.fn.select2 === "undefined"
    ) {
      return;
    }

    const $select = window.$(selectEl);
    if ($select.hasClass("select2-hidden-accessible")) {
      $select.select2("destroy");
    }

    const placeholder =
      selectEl.dataset.searchPlaceholder ||
      selectEl.querySelector("option[value='']")?.textContent?.trim() ||
      "Search and select";

    $select.select2({
      dropdownParent: window.$(modalEl),
      width: "100%",
      placeholder,
      allowClear: true,
      minimumResultsForSearch: 0,
    });
  }

  function refreshSearchableSelects() {
    getSearchableSelects().forEach(enhanceSearchableSelect);
  }

  // grab client name from a data-attribute on the modal wrapper
  const clientName = modalEl.dataset.clientName || "";

  const catSel = modalEl.querySelector("[name='category']");
  const svcSel = modalEl.querySelector("[name='service']");
  const dateInp = modalEl.querySelector("[name='scheduled_date']");
  const previewTA = modalEl.querySelector("[name='dispatch_preview']");
  const procSec = modalEl.querySelector(".processCostSection");
  const priceSec = modalEl.querySelector(".totalPriceOverride");
  const groundDiv = modalEl.querySelector("#groundFields, .ground-fields");
  const durationInp = modalEl.querySelector("[name='expected_duration_days']");

  function toggleGroundFields(isGround) {
    if (!groundDiv) return;
    groundDiv.style.display = isGround ? "block" : "none";
  }

  function updateDispatchPreview() {
    if (!previewTA || !dateInp) return;

    // only build preview if it's a ground service and both fields have values
    if (catSel.value !== "ground" || !svcSel.value || !dateInp.value) {
      // leave existing preview alone if user has typed; otherwise clear
      if (!previewTA.dataset.userEdited) {
        previewTA.value = "";
      }
      return;
    }

    // format the date-local value into a readable string
    const dt = new Date(dateInp.value);
    const when = dt.toLocaleString(undefined, {
      weekday: "long",
      day: "2-digit",
      month: "long",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

    const svcName = svcSel.options[svcSel.selectedIndex]?.text || "";

    const message = `Hi ${clientName}, surveyors for ${svcName} have been scheduled for ${when}.`;

    // only overwrite the preview if user hasn't manually changed it
    if (!previewTA.dataset.userEdited) {
      previewTA.value = message;
    }
  }

  // mark if user types to prevent auto‐overwrite
  if (previewTA) {
    previewTA.addEventListener("input", () => {
      previewTA.dataset.userEdited = "true";
    });
  }

  // when the category changes, reset things
  catSel.addEventListener("change", async () => {
    await loadServicesByCategory(catSel.value, svcSel);
    refreshSearchableSelects();
    procSec.style.display = "none";
    priceSec.style.display = "none";
    modalEl.querySelectorAll(".service-assignee-group").forEach((group) => {
      group.style.display = "";
    });
    toggleGroundFields(false);
    if (previewTA) {
      previewTA.value = "";
      delete previewTA.dataset.userEdited;
    }
  });

  // when service changes, show fields and recompute
  svcSel.addEventListener("change", async () => {
    const isGround = catSel.value === "ground";
    toggleGroundFields(isGround);
    await loadProcessesForService(svcSel.value, modalEl);
    refreshSearchableSelects();
    updateDispatchPreview();
  });

  modalEl.addEventListener(
    "process-assignees-rendered",
    refreshSearchableSelects,
  );

  if (durationInp) {
    durationInp.addEventListener("input", () => {
      durationInp.dataset.autoFill = "false";
    });
  }

  // when date changes, recompute
  if (dateInp) {
    dateInp.addEventListener("change", updateDispatchPreview);
  }

  modalEl.addEventListener("shown.bs.modal", refreshSearchableSelects);
  modalEl.addEventListener("hidden.bs.modal", () => {
    if (
      typeof window.$ === "undefined" ||
      typeof window.$.fn.select2 === "undefined"
    ) {
      return;
    }

    getSearchableSelects().forEach((selectEl) => {
      const $select = window.$(selectEl);
      if ($select.hasClass("select2-hidden-accessible")) {
        $select.select2("close");
      }
    });
  });
}

// export function initServiceModal(modalSelector) {
//     const modalEl = document.querySelector(modalSelector);
//     if (!modalEl) return;
//
//     const catSel = modalEl.querySelector("[name='category']");
//     const svcSel = modalEl.querySelector("[name='service']");
//
//     function toggleGroundFields(isGround) {
//         const gf = modalEl.querySelector("#groundFields");
//         if (gf) gf.style.display = isGround ? "block" : "none";
//     }
//
//     catSel?.addEventListener("change", () => {
//         loadServicesByCategory(catSel.value, svcSel);
//         modalEl.querySelector(".processCostSection").style.display = "none";
//         modalEl.querySelector(".totalPriceOverride").style.display = "none";
//         toggleGroundFields(false);
//     });
//
//     svcSel?.addEventListener("change", () => {
//         const isGround = catSel.value === 'ground';
//         toggleGroundFields(isGround);
//         loadProcessesForService(svcSel.value, modalEl);
//     });
// }
