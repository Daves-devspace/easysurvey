"use strict";

/**
 * DASHBOARD ANALYTICS SCRIPT
 * Handles both: Revenue Chart + Service Analysis Chart
 * Safe for use on multiple dashboard pages.
 */
document.addEventListener("DOMContentLoaded", () => {
  const getResponsiveChartHeight = (desktopHeight = 430) => {
    if (window.innerWidth < 576) return 300;
    if (window.innerWidth < 992) return 360;
    return desktopHeight;
  };

  // ================================
  // 1️⃣ REVENUE CHART SECTION
  // ================================
  const revenueChartContainer = document.querySelector(
    "#survey-sales-report-chart",
  );
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
          height: getResponsiveChartHeight(430),
          width: "100%",
          parentHeightOffset: 0,
          toolbar: { show: false },
          redrawOnWindowResize: true,
          redrawOnParentResize: true,
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
          fontSize: "12px",
          itemMargin: { horizontal: 8, vertical: 4 },
        },
        colors: ["#faad14", "#1890ff"],
        series: [
          { name: "Net Profit", data: netProfit },
          { name: "Revenue", data: revenue },
        ],
        xaxis: {
          categories: labels,
          labels: {
            hideOverlappingLabels: true,
            trim: true,
          },
        },
        responsive: [
          {
            breakpoint: 992,
            options: {
              chart: { height: getResponsiveChartHeight(430) },
              legend: {
                position: "bottom",
                horizontalAlign: "left",
              },
            },
          },
          {
            breakpoint: 576,
            options: {
              chart: { height: getResponsiveChartHeight(430) },
              plotOptions: {
                bar: {
                  columnWidth: "72%",
                },
              },
              legend: {
                position: "bottom",
                horizontalAlign: "center",
                fontSize: "11px",
              },
              xaxis: {
                labels: {
                  rotate: -45,
                  hideOverlappingLabels: true,
                  trim: true,
                  style: { fontSize: "10px" },
                },
              },
            },
          },
        ],
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

    yearSelector.addEventListener("change", (e) =>
      fetchChartData(e.target.value),
    );

    const revenueTab = document.getElementById("revenue-tab");
    revenueTab?.addEventListener("shown.bs.tab", () => {
      if (chart) {
        chart.updateOptions(
          {
            chart: {
              height: getResponsiveChartHeight(430),
              parentHeightOffset: 0,
            },
          },
          true,
          false,
        );
      }
    });

    window.addEventListener("resize", () => {
      if (chart) {
        chart.updateOptions(
          {
            chart: {
              height: getResponsiveChartHeight(430),
              parentHeightOffset: 0,
            },
          },
          true,
          false,
        );
      }
    });

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

        const defaultYear = years.includes(currentYear)
          ? currentYear
          : years[0];
        renderServiceChart(defaultYear, "", chartType);
      })
      .catch((err) => console.error("Init filters error:", err));

    // ---- Event listeners ----
    [svcYearSelector, svcSelector, chartTypeDropdown].forEach((el) => {
      el.addEventListener("change", () => {
        chartType = chartTypeDropdown.value;
        renderServiceChart(
          parseInt(svcYearSelector.value, 10),
          svcSelector.value,
          chartType,
        );
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
            data.total_revenue,
          ).toLocaleString()}`;

          const base = {
            chart: {
              height: getResponsiveChartHeight(430),
              width: "100%",
              parentHeightOffset: 0,
              type,
              stacked: type === "bar",
              toolbar: { show: true },
              redrawOnWindowResize: true,
              redrawOnParentResize: true,
            },
            title: {
              text: "Monthly Service Revenue",
              align: "left",
              style: { fontSize: "14px", fontWeight: 600 },
            },
            xaxis: {
              categories: data.labels,
              labels: {
                hideOverlappingLabels: true,
                trim: true,
              },
            },
            legend: {
              position: "bottom",
              horizontalAlign: "left",
              fontSize: "12px",
              itemMargin: { horizontal: 8, vertical: 4 },
            },
            tooltip: {
              y: {
                formatter: (val) =>
                  `${data.currency} ${Number(val).toLocaleString()}`,
              },
            },
            responsive: [
              {
                breakpoint: 992,
                options: {
                  chart: {
                    height: getResponsiveChartHeight(430),
                  },
                },
              },
              {
                breakpoint: 576,
                options: {
                  chart: {
                    height: getResponsiveChartHeight(430),
                    toolbar: { show: false },
                  },
                  legend: {
                    position: "bottom",
                    horizontalAlign: "center",
                    fontSize: "10px",
                  },
                  xaxis: {
                    labels: {
                      rotate: -45,
                      hideOverlappingLabels: true,
                      trim: true,
                      style: { fontSize: "10px" },
                    },
                  },
                },
              },
            ],
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
                      {
                        from: 10001,
                        to: 100000,
                        color: "#00A100",
                        name: "Medium",
                      },
                      {
                        from: 100001,
                        to: 1000000,
                        color: "#007700",
                        name: "High",
                      },
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

    const servicesTab = document.getElementById("services-tab");
    servicesTab?.addEventListener("shown.bs.tab", () => {
      if (chartInstance) {
        setTimeout(() => {
          chartInstance.updateOptions(
            {
              chart: {
                height: getResponsiveChartHeight(430),
                parentHeightOffset: 0,
              },
            },
            true,
            false,
          );
        }, 50);
      }
    });

    window.addEventListener("resize", () => {
      if (chartInstance) {
        chartInstance.updateOptions(
          {
            chart: {
              height: getResponsiveChartHeight(430),
              parentHeightOffset: 0,
            },
          },
          true,
          false,
        );
      }
    });
  }
});
