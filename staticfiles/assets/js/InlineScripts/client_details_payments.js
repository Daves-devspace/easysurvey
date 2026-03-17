// ==========================
// Filter Toggle & AJAX Load
// ==========================
document.addEventListener("DOMContentLoaded", function () {
  const filterForm = document.getElementById("filterForm");
  const toggleBtn = document.getElementById("toggleFilterBtn");
  const filterPanel = document.getElementById("filterPanel");
  const paymentTableWrapper = document.getElementById("paymentTableWrapper");

  const urlParams = new URLSearchParams(window.location.search);
  if (
    urlParams.has("service") ||
    urlParams.has("start_date") ||
    urlParams.has("end_date")
  ) {
    filterPanel.classList.add("show");
  }

  toggleBtn?.addEventListener("click", () => {
    filterPanel.classList.toggle("show");
  });

  filterForm?.addEventListener("submit", function (e) {
    e.preventDefault();
    const params = new URLSearchParams(new FormData(filterForm)).toString();
    fetch(`${window.location.pathname}?${params}`, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((res) => res.json())
      .then((json) => {
        const data = json.payment_history;
        if (!data.length) {
          paymentTableWrapper.innerHTML = `<div class="text-center p-3">No payment history available.</div>`;
          return;
        }

        let html = `
                    <table class="table table-bordered table-hover mb-0">
                        <thead class="table-light">
                            <tr>
                                <th>#</th>
                                <th>Service</th>
                                <th>Amount</th>
                                <th>Method</th>
                                <th>Reason</th>
                                <th>Paid On</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>`;

        data.forEach((p, i) => {
          let badgeClass = "bg-danger",
            badgeText = "Not Paid";
          if (p.payment_status === "Fully Paid") {
            badgeClass = "bg-success";
            badgeText = "Fully Paid";
          } else if (p.payment_status === "Partially Paid") {
            badgeClass = "bg-warning text-dark";
            badgeText = "Partially Paid";
          }

          html += `
                        <tr>
                            <td>${i + 1}</td>
                            <td>${p.service_name}</td>
                            <td>KES ${p.amount}</td>
                            <td>${p.method}</td>
                            <td>${p.reason}</td>
                            <td>${p.timestamp}</td>
                            <td><span class="badge ${badgeClass}">${badgeText}</span></td>
                        </tr>`;
        });

        html += `</tbody></table>`;
        paymentTableWrapper.innerHTML = html;
      });
  });
});

// ============================================
// Add SubService Modal - Autofill Modal Fields
// ============================================
// document.addEventListener("DOMContentLoaded", function () {
//     const modal = document.getElementById("addSubServiceModal");
//
//     modal?.addEventListener("show.bs.modal", function (event) {
//         const button = event.relatedTarget;
//         const serviceId = button.getAttribute("data-service-id");
//         const serviceName = button.getAttribute("data-service-name");
//
//         document.getElementById("modal_service_id").value = serviceId;
//         document.getElementById("modal_service_name").textContent = serviceName;
//
//         // Reset the fields when modal opens
//         document.getElementById("modal_sub_service").selectedIndex = 0;
//         document.getElementById("modal_department").value = '';
//         document.getElementById("modal_overridden_price").value = '';
//     });
//
//     // When a subservice is selected
//     const subServiceSelect = document.getElementById("modal_sub_service");
//     subServiceSelect?.addEventListener("change", function () {
//         const selectedOption = this.options[this.selectedIndex];
//
//         const price = selectedOption.getAttribute("data-price");
//         const department = selectedOption.getAttribute("data-dept");
//
//         console.log("Selected option:", selectedOption);
//         console.log("Data-price:", price);
//         console.log("Data-dept:", department);
//
//         document.getElementById("modal_overridden_price").value = price || '';
//         document.getElementById("modal_department").value = department || '';
//     });
// });
// ====================================================
// Payment Modal - Display Service Info When Selected
// ====================================================
document.addEventListener("DOMContentLoaded", function () {
  const addPaymentModal = document.getElementById("addPaymentModal");
  const serviceSelect = document.getElementById("serviceSelect");
  const serviceInfo = document.getElementById("serviceInfo");

  const nameSpan = document.getElementById("serviceName");
  const totalSpan = document.getElementById("serviceTotal");
  const paidSpan = document.getElementById("servicePaid");
  const pendingSpan = document.getElementById("servicePending");

  if (!serviceSelect) return;

  const initialServiceOptionsHtml = serviceSelect.innerHTML;

  function normalizePaymentServiceSelect() {
    if (
      typeof window.$ !== "undefined" &&
      typeof window.$.fn.select2 !== "undefined"
    ) {
      const $select = window.$(serviceSelect);
      if ($select.hasClass("select2-hidden-accessible")) {
        try {
          $select.select2("destroy");
        } catch (error) {
          console.warn(
            "Unable to destroy stale payment select2 instance",
            error,
          );
        }
      }
    }

    const wrappers =
      serviceSelect.parentElement?.querySelectorAll(".select2-container") || [];
    wrappers.forEach((wrapper) => wrapper.remove());

    serviceSelect.classList.remove("select2-hidden-accessible");
    serviceSelect.removeAttribute("data-select2-id");
    serviceSelect.style.display = "";
    serviceSelect.style.width = "";

    if (serviceSelect.options.length <= 1 && initialServiceOptionsHtml.trim()) {
      serviceSelect.innerHTML = initialServiceOptionsHtml;
    }
  }

  normalizePaymentServiceSelect();
  addPaymentModal?.addEventListener(
    "show.bs.modal",
    normalizePaymentServiceSelect,
  );

  serviceSelect.addEventListener("change", function () {
    const selectedOption = this.options[this.selectedIndex];

    if (selectedOption && selectedOption.value) {
      const total = parseFloat(selectedOption.dataset.total || "0");
      const paid = parseFloat(selectedOption.dataset.paid || "0");
      const pending = total - paid;

      nameSpan.innerText =
        selectedOption.label || selectedOption.textContent.trim();
      totalSpan.innerText = total.toFixed(2);
      paidSpan.innerText = paid.toFixed(2);
      pendingSpan.innerText = pending.toFixed(2);

      serviceInfo.style.display = "block";
    } else {
      serviceInfo.style.display = "none";
      nameSpan.innerText =
        totalSpan.innerText =
        paidSpan.innerText =
        pendingSpan.innerText =
          "";
    }
  });
});

// ==========================================
// Service Row Click - Populate Detail Panel
// ==========================================
document.addEventListener("DOMContentLoaded", function () {
  const rows = Array.from(document.querySelectorAll(".service-row"));
  const detail = (id) => document.getElementById(id);

  function renderDetail(data) {
    // Core fields
    detail("detail-land").innerText = data.land_description;
    detail("detail-title").innerText = data.service_name;
    detail("detail-requested").innerText = data.requested_at;
    detail("detail-paid").innerText = `KES ${data.total_paid}`;
    detail("detail-balance").innerText = `KES ${data.total_balance}`;

    // Processes
    const procUl = detail("detail-processes");
    procUl.innerHTML = "";
    data.processes.forEach((p) => {
      const li = document.createElement("li");
      li.className = "list-group-item";
      li.innerHTML = `
                <div class="d-flex justify-content-between">
                    <div>
                        <strong>${p.name}</strong><br>
                        Cost: KES ${p.cost}<br>
                        Paid: KES ${p.paid}<br>
                        Pending: KES ${p.pending}
                    </div>
                    <div>
                        <span class="badge ${p.status === "completed" ? "bg-success" : "bg-primary"}">
                            ${p.status.replace("_", " ")}
                        </span>
                    </div>
                </div>
            `;
      procUl.appendChild(li);
    });

    // Sub-services
    const subUl = detail("detail-subservices");
    subUl.innerHTML = "";
    data.sub_services.forEach((s) => {
      const li = document.createElement("li");
      li.className = "list-group-item";
      li.innerHTML = `
                <div>
                    <strong>${s.name}</strong> <small class="text-muted">(${s.added_on})</small><br>
                    Price: KES ${s.price}<br>
                    Paid: KES ${s.paid}<br>
                    Balance: KES ${s.balance}
                </div>
            `;
      subUl.appendChild(li);
    });
  }

  rows.forEach((row) => {
    row.addEventListener("click", function () {
      const jsonData = JSON.parse(this.dataset.detail);
      renderDetail(jsonData);
    });
  });
});

// ==========================================
// validation error in payment modal
// ==========================================
// document.addEventListener("DOMContentLoaded", function () {
//     const amountInput = document.getElementById("amount");
//     const serviceSelect = document.getElementById("serviceSelect");
//
//     serviceSelect.addEventListener("change", function () {
//         const selected = this.options[this.selectedIndex];
//         amountInput.max = selected?.dataset.pending || "";
//     });
//
//     document.querySelector("form").addEventListener("submit", function (e) {
//         const selected = serviceSelect.options[serviceSelect.selectedIndex];
//         const pending = parseFloat(selected.dataset.pending || "0");
//         const entered = parseFloat(amountInput.value || "0");
//
//         if (entered > pending) {
//             e.preventDefault();
//             alert(`Payment exceeds the pending balance of KES ${pending.toFixed(2)}.`);
//         }
//     });
// });
//
// main.js (ES6 Module)

// === RECEIPT MODAL HANDLER ===
const ReceiptModal = (() => {
  let modal, iframe, downloadBtn, printBtn, detailBtn, currentUrl;

  function init() {
    modal = new bootstrap.Modal(document.getElementById("receiptModal"));
    iframe = document.getElementById("receiptFrame");
    downloadBtn = document.getElementById("downloadReceiptBtn");
    printBtn = document.getElementById("printReceiptBtn");
    detailBtn = document.getElementById("detailReceiptBtn");
    currentUrl = null;

    setupRowButtons();
    setupDetailButton();
    setupPrint();
    setupAutoReset();
  }

  function setupRowButtons() {
    document.querySelectorAll(".service-row").forEach((row) => {
      const btn = row.querySelector(".btn-receipt");
      const url = row.dataset.receiptUrl;
      if (btn && url) {
        btn.addEventListener("click", () => {
          currentUrl = url;
          openModal(url);
        });
      }
    });
  }

  function setupDetailButton() {
    if (detailBtn) {
      detailBtn.addEventListener("click", () => {
        if (!currentUrl)
          return alert("Please click a row’s Preview button first.");
        openModal(currentUrl);
      });
    }
  }

  function setupPrint() {
    if (printBtn) {
      printBtn.addEventListener("click", () => {
        iframe.contentWindow.focus();
        iframe.contentWindow.print();
      });
    }
  }

  function setupAutoReset() {
    document
      .getElementById("receiptModal")
      .addEventListener("hidden.bs.modal", () => {
        iframe.src = "";
        currentUrl = null;
      });
  }

  function openModal(url) {
    iframe.src = url;
    downloadBtn.href = url;
    modal.show();
  }

  return { init };
})();

// === PAYMENT VALIDATION ===
const PaymentValidation = (() => {
  function init() {
    const modal = document.getElementById("addPaymentModal");
    const amountInput = document.getElementById("amount");
    const serviceSelect = document.getElementById("serviceSelect");
    const form = modal?.querySelector("form");

    if (!amountInput || !serviceSelect || !form) return;

    serviceSelect.addEventListener("change", () => {
      const selected = serviceSelect.options[serviceSelect.selectedIndex];
      amountInput.max = selected?.dataset.pending || "";
    });

    form.addEventListener("submit", (e) => {
      const selected = serviceSelect.options[serviceSelect.selectedIndex];
      const pending = parseFloat(selected.dataset.pending || "0");
      const entered = parseFloat(amountInput.value || "0");

      if (entered > pending) {
        e.preventDefault();
        alert(
          `Payment exceeds the pending balance of KES ${pending.toFixed(2)}.`,
        );
      }
    });
  }

  return { init };
})();

// === INIT ALL ===
document.addEventListener("DOMContentLoaded", () => {
  ReceiptModal.init();
  PaymentValidation.init();
});
