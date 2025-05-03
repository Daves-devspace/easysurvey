'use strict';
// sales.js
document.addEventListener('DOMContentLoaded', () => {
  const chartContainer = document.querySelector('#survey-sales-report-chart');
  const yearSelector = document.getElementById('year-selector');
  const exportBtn = document.getElementById('export-csv-btn');
  let chartData = { labels: [], net_profit: [], revenue: [] };
  let chart;

  async function fetchYears() {
    try {
      const res = await fetch('/api/available-years/');
      const data = await res.json();
      yearSelector.innerHTML = data.years.map(year =>
        `<option value="${year}">${year}</option>`
      ).join('');
      fetchChartData(data.years[0]);
    } catch (err) {
      console.error('Error loading years:', err);
    }
  }

  async function fetchChartData(year) {
    try {
      const res = await fetch(`/api/chart-data/?year=${year}`);
      const data = await res.json();
      chartData = data;
      renderChart(data.labels, data.net_profit, data.revenue);
    } catch (err) {
      console.error('Error loading chart data:', err);
    }
  }

  function renderChart(labels, netProfit, revenue) {
  const options = {
    chart: {
      type: 'bar',
      height: 430,
      toolbar: { show: false }
    },
    plotOptions: {
      bar: {
        columnWidth: '60%',  // Increased from 30% to 60%
        borderRadius: 4
      }
    },
    stroke: {
      show: true,
      width: 8,
      colors: ['transparent']
    },
    dataLabels: { enabled: false },
    legend: {
      position: 'top',
      horizontalAlign: 'right',
      show: true,
      fontFamily: `'Public Sans', sans-serif`
    },
    colors: ['#faad14', '#1890ff'],
    series: [
      { name: 'Net Profit', data: netProfit },
      { name: 'Revenue', data: revenue }
    ],
    xaxis: { categories: labels }
  };

  if (chart) {
    chart.updateOptions(options);
  } else {
    chart = new ApexCharts(chartContainer, options);
    chart.render();
  }
}


  // CSV Export logic
  exportBtn.addEventListener('click', () => {
    const { labels, net_profit, revenue } = chartData;
    let csv = "Month,Net Profit,Revenue\n";
    labels.forEach((month, index) => {
      csv += `${month},${net_profit[index]},${revenue[index]}\n`;
    });

    const blob = new Blob([csv], { type: "text/csv" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report_${yearSelector.value}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  });

  yearSelector.addEventListener('change', (e) => {
    fetchChartData(e.target.value);
  });

  fetchYears();
});




document.addEventListener('DOMContentLoaded', function() {
  // DOM elements
  const yearSelector    = document.getElementById('service-year-selector');
  const serviceSelector = document.getElementById('service-selector');
  const summaryYear     = document.getElementById('summary-year');
  const summaryServices = document.getElementById('summary-services');
  const summaryRevenue  = document.getElementById('summary-revenue');
  const chartContainer  = document.getElementById('chart');
  let chartInstance = null;

  // Month labels fallback
  const monthLabels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const currentYear = new Date().getFullYear();

  // Fetch available years and services
  Promise.all([
    fetch('/api/available-years/').then(res => res.json()),
    fetch('/api/services/').then(res => res.json())
  ])
  .then(([yearData, svcData]) => {
    // Populate year dropdown
    const years = yearData.years.sort((a, b) => b - a);
    years.forEach(y => {
      const opt = document.createElement('option');
      opt.value = opt.textContent = y;
      if (y === currentYear) opt.selected = true;
      yearSelector.appendChild(opt);
    });

    // Populate service dropdown
    // Add 'All Services' option
    const allOpt = document.createElement('option');
    allOpt.value = '';
    allOpt.textContent = 'All Services';
    serviceSelector.appendChild(allOpt);

    svcData.services
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.name;
        serviceSelector.appendChild(opt);
      });

    // Render initial chart
    const defaultYear = years.includes(currentYear) ? currentYear : years[0];
    renderChart(defaultYear, '');
  })
  .catch(err => console.error('Error initializing filters:', err));

  // Re-render on filter change
  [yearSelector, serviceSelector].forEach(el => {
    el.addEventListener('change', () => {
      renderChart(parseInt(yearSelector.value, 10), serviceSelector.value);
    });
  });

  function renderChart(year, serviceId) {
    // Update summary year immediately
    summaryYear.textContent = `Year: ${year}`;

    // Destroy existing chart
    if (chartInstance) {
      chartInstance.destroy();
      chartContainer.innerHTML = '';
    }

    // Build API URL
    let url = `/api/analysis/monthly-services/?year=${year}`;
    if (serviceId) url += `&service_id=${serviceId}`;

    fetch(url)
      .then(res => res.json())
      .then(data => {
        // Fallback to fixed month labels if none returned
        const labels = (data.labels && data.labels.length) ? data.labels : monthLabels;

        // Update summary details
        summaryServices.textContent = `Services Analyzed: ${data.total_services}`;
        summaryRevenue.textContent  = `Total Revenue: ${data.currency} ${Number(data.total_revenue).toLocaleString()}`;

        // ApexCharts options
        const options = {
          series: data.series,
          chart: { type: 'bar', height: 430, stacked: true, toolbar: { show: true } },
          plotOptions: {
            bar: {
              horizontal: true,
              dataLabels: { total: { enabled: true, style: { fontSize: '13px', fontWeight: 600 } } }
            }
          },
          yaxis: { categories: labels },
          xaxis: { labels: { formatter: val => `${data.currency} ${Number(val).toLocaleString()}` } },
          tooltip: { y: { formatter: val => `${data.currency} ${Number(val).toLocaleString()}` } },
          legend: { position: 'top', horizontalAlign: 'center' },
          fill: { opacity: 1 },
          colors: ['#2E8B57', '#4682B4', '#FF8C00', '#A52A2A', '#8A2BE2', '#20B2AA']
        };

        // Render chart
        chartInstance = new ApexCharts(chartContainer, options);
        chartInstance.render();
      })
      .catch(err => console.error('Error fetching chart data:', err));
  }
});


























