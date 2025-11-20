// lease-modal.js - vanilla JS, optimized, reusable for create & update
(function () {
  "use strict";

  // Config
  const API_SEARCH = "/tenant_management/api/units/search/"; // adjust if needed
  const DEBOUNCE_MS = 250;
  const PAGE_SIZE = 25;

  // Utilities
  function qs(sel, root = document) {
    return root.querySelector(sel);
  }
  function qsa(sel, root = document) {
    return Array.from(root.querySelectorAll(sel));
  }
  function el(tag, cls) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }
  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
  }
  const csrftoken = getCookie("csrftoken");

  // Debounce
  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  // Modal elements (single instance)
  const modalEl = qs("#leaseModal");
  if (!modalEl) return; // nothing to do
  const modal = new bootstrap.Modal(modalEl, { backdrop: "static" });
  const form = qs("#leaseModalForm", modalEl);
  const searchInput = qs("#leaseModalSearch", modalEl);
  const resultsWrap = qs("#leaseModalResults", modalEl);
  const tenantNameInput = qs("#leaseModalTenantName", modalEl);
  const tenantIdHidden = qs("#leaseModalTenantId", modalEl);
  const unitIdHidden = qs("#leaseModalUnitId", modalEl);
  const submitTemplateHidden = qs("#leaseModalSubmitTemplate", modalEl);
  const modeHidden = qs("#leaseModalMode", modalEl);

  const startDate = qs("#leaseModalStartDate", modalEl);
  const depositInput = qs("#leaseModalDeposit", modalEl);
  const details = {
    unit: qs("#detailUnit", modalEl),
    property: qs("#detailProperty", modalEl),
    rent: qs("#detailRent", modalEl),
    meter: qs("#detailMeter", modalEl),
    note: qs("#detailNote", modalEl),
    help: qs("#leaseModalHelp", modalEl),
    error: qs("#leaseModalError", modalEl),
  };

  const submitBtn = qs("#leaseModalSubmit", modalEl);

  // state
  let searchState = {
    q: "",
    page: 1,
    loading: false,
    more: false,
    items: [],
    highlightIndex: -1,
    propertyId: null,
  };

  // Render helpers
  function clearResults() {
    resultsWrap.innerHTML = "";
    searchState.items = [];
    searchState.page = 1;
    searchState.more = false;
    searchState.highlightIndex = -1;
  }
  function showError(msg) {
    details.error.classList.remove("d-none");
    details.error.textContent = msg;
  }
  function clearError() {
    details.error.classList.add("d-none");
    details.error.textContent = "";
  }
  function renderItem(item, index) {
    const a = el("button", "list-group-item list-group-item-action text-start");
    a.type = "button";
    a.dataset.idx = index;
    a.dataset.id = item.id;
    a.innerHTML = `<div class="fw-semibold">${escapeHtml(
      item.unit_number || item.text
    )}</div>
                   <div class="small text-muted">${escapeHtml(
                     item.property_name || ""
                   )} • Rent: ${formatNumber(item.rent)}</div>`;
    return a;
  }
  function renderLoadMore() {
    const btn = el(
      "button",
      "list-group-item list-group-item-action text-center"
    );
    btn.type = "button";
    btn.id = "leaseLoadMore";
    btn.textContent = "Load more…";
    return btn;
  }
  function escapeHtml(s) {
    if (!s && s !== 0) return "";
    return String(s).replace(/[&<>"'`=\/]/g, function (c) {
      return "&#" + c.charCodeAt(0) + ";";
    });
  }
  function formatNumber(v) {
    if (!v && v !== 0) return "—";
    try {
      return Number(v).toLocaleString();
    } catch (e) {
      return v;
    }
  }

  // Populate details card
  function fillDetails(item) {
    details.unit.textContent = item.unit_number || item.text || "—";
    details.property.textContent = item.property_name || "—";
    details.rent.textContent = item.rent ? formatNumber(item.rent) : "—";
    details.meter.textContent = item.meter_number || "—";
    details.note.textContent =
      "Click Assign to create the lease for this unit.";
  }
  function resetDetails() {
    details.unit.textContent = "—";
    details.property.textContent = "—";
    details.rent.textContent = "—";
    details.meter.textContent = "—";
    details.note.textContent = "Select a unit to see details.";
  }

  // Fetcher for search
  async function fetchSearch(q, page = 1) {
    if (searchState.loading) return;
    searchState.loading = true;
    clearError();
    const params = new URLSearchParams();
    params.set("q", q || "");
    params.set("page", page);
    params.set("page_size", PAGE_SIZE);
    if (searchState.propertyId)
      params.set("property_id", searchState.propertyId);

    try {
      const res = await fetch(API_SEARCH + "?" + params.toString(), {
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("Search failed");
      const json = await res.json();
      return json;
    } catch (err) {
      showError("Search error. See console.");
      console.error(err);
      return { results: [], pagination: { more: false } };
    } finally {
      searchState.loading = false;
    }
  }

  // Render results page (append)
  function appendResults(results, more) {
    // append items
    const frag = document.createDocumentFragment();
    const startIdx = searchState.items.length;
    results.forEach((it, i) => {
      const node = renderItem(it, startIdx + i);
      frag.appendChild(node);
    });
    // remove existing load-more if present
    const existingLoad = qs("#leaseLoadMore", resultsWrap);
    if (existingLoad) existingLoad.remove();

    resultsWrap.appendChild(frag);
    searchState.items = searchState.items.concat(results);
    searchState.more = !!more;
    if (more) resultsWrap.appendChild(renderLoadMore());
  }

  // Select item
  function selectItemByIndex(idx) {
    if (idx < 0 || idx >= searchState.items.length) return;
    // remove highlight from previous
    qsa(".list-group-item.active", resultsWrap).forEach((n) =>
      n.classList.remove("active")
    );
    const node = resultsWrap.querySelector(`[data-idx="${idx}"]`);
    if (!node) return;
    node.classList.add("active");
    node.scrollIntoView({ block: "nearest" });
    searchState.highlightIndex = idx;
    const item = searchState.items[idx];
    unitIdHidden.value = item.id;
    fillDetails(item);
  }

  // Public: open modal with config from trigger element
  function openLeaseModal(trigger) {
    // trigger is the clicked element with data- attributes
    const mode = (trigger.dataset.mode || "create").toLowerCase();
    const tenantId = trigger.dataset.tenantId || "";
    const tenantName = trigger.dataset.tenantName || "";
    const propertyId = trigger.dataset.propertyId || "";
    const submitTemplate = trigger.dataset.submitTemplate || ""; // for create
    const submitUrl = trigger.dataset.submitUrl || ""; // for update
    const initialUnitId = trigger.dataset.initialUnitId || "";
    const initialStart = trigger.dataset.initialStartDate || "";
    const initialDeposit = trigger.dataset.initialDeposit || "";
    const insertTarget = trigger.dataset.insertTarget || "";

    // store mode & config
    modeHidden.value = mode;
    tenantIdHidden.value = tenantId;
    tenantNameInput.value = tenantName;
    searchState.propertyId = propertyId || null;
    submitTemplateHidden.value = submitTemplate || submitUrl || "";

    // preset start/deposit
    startDate.value = initialStart || new Date().toISOString().slice(0, 10);
    depositInput.value = initialDeposit || "";

    // reset search UI
    clearResults();
    resetDetails();
    clearError();
    unitIdHidden.value = "";

    // if initialUnitId provided (update flow), fetch that unit details from search endpoint (single lookup)
    if (initialUnitId) {
      // try to find in local results first; if none, fetch single unit by q= and property filter (fast)
      (async function loadInitialUnit() {
        const data = await fetchSearch("", 1); // best-effort; server should allow searching by id by sending q param = id maybe
        // try to find by id
        let found = null;
        if (data && Array.isArray(data.results)) {
          found = data.results.find(
            (r) => String(r.id) === String(initialUnitId)
          );
        }
        if (!found) {
          // fallback: call API with q= unit id
          const byId = await fetchSearch(initialUnitId, 1);
          if (byId && byId.results && byId.results.length)
            found = byId.results.find(
              (r) => String(r.id) === String(initialUnitId)
            );
        }
        if (found) {
          searchState.items = [found];
          appendResults([found], false);
          selectItemByIndex(0);
        }
      })();
    }

    // open modal
    modal.show();

    // store insert target for after-success insert
    modalEl.dataset.insertTarget = insertTarget || "";
  }

  // wire clicks on results
  resultsWrap.addEventListener("click", function (ev) {
    const btn = ev.target.closest("[data-idx]");
    if (!btn) return;
    const idx = Number(btn.dataset.idx);
    selectItemByIndex(idx);
  });

  // keyboard support inside results wrap
  searchInput.addEventListener("keydown", function (ev) {
    const key = ev.key;
    if (key === "ArrowDown") {
      ev.preventDefault();
      const next = Math.min(
        searchState.highlightIndex + 1,
        searchState.items.length - 1
      );
      selectItemByIndex(next);
    } else if (key === "ArrowUp") {
      ev.preventDefault();
      const prev = Math.max(searchState.highlightIndex - 1, 0);
      selectItemByIndex(prev);
    } else if (key === "Enter") {
      ev.preventDefault();
      if (searchState.highlightIndex >= 0)
        selectItemByIndex(searchState.highlightIndex);
      // focus submit when enter selected
      submitBtn.focus();
    }
  });

  // load more click
  resultsWrap.addEventListener("click", function (ev) {
    const load = ev.target.closest("#leaseLoadMore");
    if (!load) return;
    // remove load-more and fetch next page
    load.remove();
    if (searchState.more) {
      const nextPage = searchState.page + 1;
      (async function () {
        const data = await fetchSearch(searchState.q, nextPage);
        if (data) {
          searchState.page = nextPage;
          appendResults(
            data.results || [],
            data.pagination && data.pagination.more
          );
        }
      })();
    }
  });

  // debounced search trigger
  const doSearch = debounce(async function (q) {
    searchState.q = q || "";
    searchState.page = 1;
    clearResults();
    if (!q || q.length < 1) return;
    const data = await fetchSearch(q, 1);
    if (data) {
      appendResults(
        data.results || [],
        data.pagination && data.pagination.more
      );
    }
  }, DEBOUNCE_MS);

  searchInput.addEventListener("input", function (ev) {
    doSearch(ev.target.value.trim());
  });

  // global open handlers: any element with .open-lease-modal
  document.addEventListener("click", function (ev) {
    const trigger = ev.target.closest(".open-lease-modal");
    if (!trigger) return;
    ev.preventDefault();
    openLeaseModal(trigger);
  });

  // submit handler: build URL and post
  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    clearError();

    const mode = modeHidden.value || "create";
    const tenantId = tenantIdHidden.value;
    const unitId = unitIdHidden.value;
    const submitTemplate = submitTemplateHidden.value || "";

    if (!unitId) {
      showError("Please select a unit first.");
      return;
    }

    // construct submit URL
    let submitUrl = submitTemplate;
    if (mode === "create") {
      // expect template containing :unit_id: and :tenant_id:
      if (
        !submitUrl.includes(":unit_id:") ||
        !submitUrl.includes(":tenant_id:")
      ) {
        showError(
          "Invalid submit template for create. Ask dev to provide /units/:unit_id:/tenant/:tenant_id:/existing-tenant-lease/"
        );
        return;
      }
      submitUrl = submitUrl
        .replace(":unit_id:", encodeURIComponent(unitId))
        .replace(":tenant_id:", encodeURIComponent(tenantId));
    } else {
      // update mode: template should be full URL (no placeholders)
      // if template contains placeholder, also replace unit and/or lease id if provided
      if (submitUrl.includes(":unit_id:"))
        submitUrl = submitUrl.replace(":unit_id:", encodeURIComponent(unitId));
    }

    // payload
    const payload = new URLSearchParams();
    if (startDate.value) payload.append("start_date", startDate.value);
    if (depositInput.value)
      payload.append("deposit_amount", depositInput.value);

    try {
      const res = await fetch(submitUrl, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrftoken,
          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        body: payload.toString(),
        credentials: "same-origin",
      });

      const json = await res.json().catch(() => null);
      if (res.ok && json && json.success) {
        // optional insert of returned html row
        const insertTarget = modalEl.dataset.insertTarget;
        if (insertTarget && json.html) {
          try {
            const container = document.querySelector(insertTarget);
            if (container) {
              const tmp = document.createElement("tbody");
              tmp.innerHTML = json.html;
              const row = tmp.querySelector("tr") || tmp.firstChild;
              if (row) container.prepend(row);
            }
          } catch (e) {
            console.warn(e);
          }
        }

        // hide modal
        modal.hide();
      } else {
        // show server-rendered form (errors) if provided
        if (json && json.html) {
          // replace modal-body with server content (server should render the form fragment)
          const newBody = el("div");
          newBody.innerHTML = json.html;
          const mb = qs(".modal-body", modalEl);
          if (mb)
            mb.replaceWith(newBody.querySelector(".modal-body") || newBody);
        } else {
          showError(
            (json && (json.message || json.error)) ||
              "Could not save. Please check input."
          );
        }
      }
    } catch (err) {
      console.error(err);
      showError("Unexpected error. See console.");
    }
  });

  // when modal hides, reset
  modalEl.addEventListener("hidden.bs.modal", function () {
    clearResults();
    resetDetails();
    searchInput.value = "";
    unitIdHidden.value = "";
    clearError();
    modalEl.dataset.insertTarget = "";
  });

  // helper to show inline error in modal footer area
  function showError(msg) {
    details.error.classList.remove("d-none");
    details.error.textContent = msg;
  }
})();
