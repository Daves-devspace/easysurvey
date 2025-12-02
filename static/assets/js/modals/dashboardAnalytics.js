"use strict";

/**
 * DASHBOARD ANALYTICS SCRIPT
 * Handles both: Revenue Chart + Service Analysis Chart
 * Safe for use on multiple dashboard pages.
 */
document.addEventListener("DOMContentLoaded", () => {
  // ================================
  // 1️⃣ REVENUE CHART SECTION
  // ================================
  const revenueChartContainer = document.querySelector("#survey-sales-report-chart");
  const yearSelector = document.getElementById("year-selector");
  const exportBtn = document.getElementById("export-csv-btn");

  if (revenueChartContainer && yearSelector && exportBtn) {
    let chartData = { labels: [], net_profit: [], revenue: [] };
    let chart;

    // ---- Fetch available years ----
    async function fetchYears() {
      try {
        const res = await fetch("/api/available-years/");
        const data = await res.json();

        yearSelector.innerHTML = data.years
          .map((year) => `<option value="${year}">${year}</option>`)
          .join("");

        if (data.years.length) {
          yearSelector.value = data.years[0];
          fetchChartData(data.years[0]);
        }
      } catch (err) {
        console.error("Error loading years:", err);
      }
    }

    // ---- Fetch and render chart data ----
    async function fetchChartData(year) {
      try {
        const res = await fetch(`/api/chart-data/?year=${year}`);
        const data = await res.json();
        chartData = data;
        renderChart(data.labels, data.net_profit, data.revenue);
      } catch (err) {
        console.error("Error loading chart data:", err);
      }
    }

    // ---- Render ApexChart ----
    function renderChart(labels, netProfit, revenue) {
      const options = {
        chart: {
          type: "bar",
          height: 430,
          toolbar: { show: false },
        },
        plotOptions: {
          bar: {
            columnWidth: "60%",
            borderRadius: 4,
          },
        },
        stroke: {
          show: true,
          width: 8,
          colors: ["transparent"],
        },
        dataLabels: { enabled: false },
        legend: {
          position: "top",
          horizontalAlign: "right",
          show: true,
          fontFamily: `'Public Sans', sans-serif`,
        },
        colors: ["#faad14", "#1890ff"],
        series: [
          { name: "Net Profit", data: netProfit },
          { name: "Revenue", data: revenue },
        ],
        xaxis: { categories: labels },
      };

      if (chart) chart.updateOptions(options);
      else {
        chart = new ApexCharts(revenueChartContainer, options);
        chart.render();
      }
    }

    // ---- CSV Export ----
    exportBtn.addEventListener("click", () => {
      const { labels, net_profit, revenue } = chartData;
      let csv = "Month,Net Profit,Revenue\n";
      labels.forEach((month, i) => {
        csv += `${month},${net_profit[i]},${revenue[i]}\n`;
      });

      const blob = new Blob([csv], { type: "text/csv" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report_${yearSelector.value}.csv`;
      a.click();
      window.URL.revokeObjectURL(url);
    });

    yearSelector.addEventListener("change", (e) => fetchChartData(e.target.value));

    fetchYears();
  }

  // ================================
  // 2️⃣ SERVICE ANALYSIS SECTION
  // ================================
  const svcYearSelector = document.getElementById("service-year-selector");
  const svcSelector = document.getElementById("service-selector");
  const svcChartContainer = document.getElementById("chart");
  const summaryYear = document.getElementById("summary-year");
  const summaryServices = document.getElementById("summary-services");
  const summaryRevenue = document.getElementById("summary-revenue");

  // Add chart type toggle
  const chartTypeDropdown = document.createElement("select");
  chartTypeDropdown.className = "form-select form-select-sm";
  chartTypeDropdown.style.minWidth = "130px";
  chartTypeDropdown.innerHTML = `
    <option value="bar" selected>Stacked Bar</option>
    <option value="line">Line</option>
    <option value="heatmap">Heatmap</option>`;
  // Append next to service filters if available
  const svcFilters = document.getElementById("service-filters");
  if (svcFilters) svcFilters.appendChild(chartTypeDropdown);

  if (svcYearSelector && svcSelector && svcChartContainer) {
    let chartInstance = null;
    let chartType = "bar";
    const currentYear = new Date().getFullYear();

    // ---- Fetch years & services ----
    Promise.all([
      fetch("/api/available-years/").then((r) => r.json()),
      fetch("/api/services/").then((r) => r.json()),
    ])
      .then(([yearData, svcData]) => {
        const years = yearData.years.sort((a, b) => b - a);
        years.forEach((y) => {
          const opt = document.createElement("option");
          opt.value = opt.textContent = y;
          if (y === currentYear) opt.selected = true;
          svcYearSelector.appendChild(opt);
        });

        const allOpt = document.createElement("option");
        allOpt.value = "";
        allOpt.textContent = "All Services";
        svcSelector.appendChild(allOpt);

        svcData.services
          .sort((a, b) => a.name.localeCompare(b.name))
          .forEach((s) => {
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.textContent = s.name;
            svcSelector.appendChild(opt);
          });

        const defaultYear = years.includes(currentYear) ? currentYear : years[0];
        renderServiceChart(defaultYear, "", chartType);
      })
      .catch((err) => console.error("Init filters error:", err));

    // ---- Event listeners ----
    [svcYearSelector, svcSelector, chartTypeDropdown].forEach((el) => {
      el.addEventListener("change", () => {
        chartType = chartTypeDropdown.value;
        renderServiceChart(parseInt(svcYearSelector.value, 10), svcSelector.value, chartType);
      });
    });

    // ---- Chart render ----
    function renderServiceChart(year, serviceId, type) {
      summaryYear.textContent = `Year: ${year}`;

      if (chartInstance) chartInstance.destroy();

      let url = `/api/analysis/monthly-services/?year=${year}`;
      if (serviceId) url += `&service_id=${serviceId}`;

      fetch(url)
        .then((r) => r.json())
        .then((data) => {
          summaryServices.textContent = `Services Analyzed: ${data.total_services}`;
          summaryRevenue.textContent = `Total Revenue: ${data.currency} ${Number(
            data.total_revenue
          ).toLocaleString()}`;

          const base = {
            chart: {
              height: 430,
              type,
              stacked: type === "bar",
              toolbar: { show: true },
            },
            title: {
              text: "Monthly Service Revenue",
              align: "left",
              style: { fontSize: "14px", fontWeight: 600 },
            },
            xaxis: { categories: data.labels },
            legend: { position: "bottom" },
            tooltip: {
              y: { formatter: (val) => `${data.currency} ${Number(val).toLocaleString()}` },
            },
          };

          let options;

          if (type === "bar") {
            options = {
              ...base,
              plotOptions: {
                bar: {
                  horizontal: false,
                  dataLabels: {
                    total: {
                      enabled: true,
                      style: { fontSize: "13px", fontWeight: 600 },
                    },
                  },
                },
              },
              yaxis: {
                title: { text: `Revenue (${data.currency})` },
                labels: { formatter: (val) => Number(val).toLocaleString() },
              },
              series: data.series,
            };
          } else if (type === "line") {
            options = {
              ...base,
              stroke: { curve: "smooth", width: 3 },
              markers: { size: 4 },
              yaxis: {
                title: { text: `Revenue (${data.currency})` },
                labels: { formatter: (val) => Number(val).toLocaleString() },
              },
              series: data.series,
            };
          } else if (type === "heatmap") {
            const heatmapSeries = data.series.map((s) => ({
              name: s.name,
              data: data.labels.map((x, i) => ({ x, y: s.data[i] })),
            }));

            options = {
              ...base,
              chart: { ...base.chart, type: "heatmap" },
              plotOptions: {
                heatmap: {
                  shadeIntensity: 0.5,
                  colorScale: {
                    ranges: [
                      { from: 0, to: 0, color: "#e0e0e0", name: "No Data" },
                      { from: 1, to: 10000, color: "#90EE90", name: "Low" },
                      { from: 10001, to: 100000, color: "#00A100", name: "Medium" },
                      { from: 100001, to: 1000000, color: "#007700", name: "High" },
                    ],
                  },
                },
              },
              series: heatmapSeries,
            };
          }

          chartInstance = new ApexCharts(svcChartContainer, options);
          chartInstance.render();
        })
        .catch((err) => console.error("Service chart error:", err));
    }
  }
});
