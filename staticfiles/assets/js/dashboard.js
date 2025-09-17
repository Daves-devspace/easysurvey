// dashboard.js

async function fetchData(url) {
  const res = await fetch(url);
  return res.json();
}

document.addEventListener("DOMContentLoaded", async () => {
  // 1. Financial KPIs → Cards
  const financial = await fetchData(
    "/tenant_management/api/dashboard/financial/"
  );
  document.getElementById("total-collected").innerText =
    financial.total_collected.toLocaleString();
  document.getElementById("outstanding").innerText =
    financial.outstanding.toLocaleString();
  document.getElementById("vacant-loss").innerText =
    financial.vacant_loss.toLocaleString();
  document.getElementById("deposits-held").innerText =
    financial.deposits_held.toLocaleString();

  // 2. Occupancy per property → Bar Chart
  const occupancy = await fetchData("/tenant_management/api/dashboard/occupancy/");
  new Chart(document.getElementById("occupancyChart"), {
    type: "bar",
    data: {
      labels: occupancy.per_property.map((p) => p.property__name),
      datasets: [
        {
          label: "Occupancy %",
          data: occupancy.per_property.map((p) => p.occupancy_rate),
          backgroundColor: "rgba(54, 162, 235, 0.6)",
        },
      ],
    },
  });

  // 3. Invoice Status → Pie Chart
  const operational = await fetchData("/tenant_management/api/dashboard/operational/");
  new Chart(document.getElementById("invoiceChart"), {
    type: "pie",
    data: {
      labels: operational.invoice_status.map((s) => s.status),
      datasets: [
        {
          data: operational.invoice_status.map((s) => s.count),
          backgroundColor: ["#4caf50", "#ff9800", "#f44336", "#2196f3"],
        },
      ],
    },
  });

  // 4. Collections Aging → Doughnut Chart
  const collections = await fetchData("/tenant_management/api/dashboard/collections/");
  new Chart(document.getElementById("agingChart"), {
    type: "doughnut",
    data: {
      labels: Object.keys(collections.aging),
      datasets: [
        {
          data: Object.values(collections.aging),
          backgroundColor: ["#81c784", "#ffb74d", "#e57373"],
        },
      ],
    },
  });

  // 5. Top 5 Tenants in Arrears → Horizontal Bar Chart
  new Chart(document.getElementById("arrearsChart"), {
    type: "bar",
    data: {
      labels: collections.top_arrears.map((t) => t.tenant__full_name),
      datasets: [
        {
          label: "Balance",
          data: collections.top_arrears.map((t) => t.balance),
          backgroundColor: "rgba(255, 99, 132, 0.6)",
        },
      ],
    },
    options: {
      indexAxis: "y",
    },
  });
});
