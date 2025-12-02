// Complete refactored ApexCharts configuration
/* global ApexCharts */

document.addEventListener('DOMContentLoaded', function () {
    // Define data arrays safely with JSON.parse
    // This is necessary to properly convert Django template variables to JavaScript arrays

    let chartLabels, chartClientsData, chartTitleDeedsData, chartRevenueData, chartExpensesData, chartNetRevenueData;

    try {
        chartLabels = JSON.parse('{{ dashboard.month_labels|escapejs }}');
        chartClientsData = JSON.parse('{{ dashboard.clients_data|escapejs }}');
        chartTitleDeedsData = JSON.parse('{{ dashboard.title_deeds_data|escapejs }}');
        chartRevenueData = JSON.parse('{{ dashboard.collected_data|escapejs }}');
        chartExpensesData = JSON.parse('{{ dashboard.expenses_data|escapejs }}');
        chartNetRevenueData = JSON.parse('{{ dashboard.net_revenue_data|escapejs }}');
    } catch (e) {
        console.error("Error parsing data from Django context:", e);
        // Fallback to empty arrays if parsing fails
        chartLabels = [];
        chartClientsData = [];
        chartTitleDeedsData = [];
        chartRevenueData = [];
        chartExpensesData = [];
        chartNetRevenueData = [];
    }

    // Helper function to create a chart point with marker
    function createChartHighlight(data) {
        const markers = Array(data.length).fill({});
        markers[markers.length - 1] = {
            size: 6,
            fillColor: '#fff',
            strokeColor: '#000',
            strokeWidth: 2
        };
        return markers;
    }

    // Common chart configuration
    function createChartConfig(selector, data, color, name, showLatestValue = true) {
        // Get latest value for tooltip
        const latestValue = data[data.length - 1];

        // Add marker to the last point
        const markers = showLatestValue ? createChartHighlight(data) : undefined;

        // Determine min and max for y-axis with some padding
        const values = data.filter(val => val !== null && val !== undefined);
        const minValue = values.length > 0 ? Math.min(...values) * 0.9 : 0;
        const maxValue = values.length > 0 ? Math.max(...values) * 1.1 : 10;

        return {
            chart: {
                type: 'line',
                height: 100,
                toolbar: { show: false },
                sparkline: { enabled: true },
                animations: {
                    enabled: true,
                    easing: 'easeinout',
                    speed: 800
                },
                background: 'transparent'
            },
            series: [{
                name: name,
                data: data
            }],
            stroke: {
                curve: 'smooth',
                width: 2.5,
                lineCap: 'round'
            },
            fill: {
                type: 'gradient',
                gradient: {
                    shadeIntensity: 1,
                    opacityFrom: 0.3,
                    opacityTo: 0.1,
                    stops: [0, 90, 100]
                }
            },
            xaxis: {
                categories: chartLabels,
                axisBorder: { show: false },
                axisTicks: { show: false },
                labels: { show: false }
            },
            yaxis: {
                min: minValue,
                max: maxValue,
                labels: { show: false }
            },
            grid: {
                show: false,
                padding: {
                    top: 5,
                    right: 0,
                    bottom: 5,
                    left: 0
                }
            },
            tooltip: {
                enabled: true,
                x: { show: true },
                y: {
                    formatter: function (value) {
                        // Format as currency if it's a monetary value
                        if (name.toLowerCase().includes('revenue') || name.toLowerCase().includes('expense')) {
                            return 'KSH ' + value.toLocaleString();
                        }
                        return value.toLocaleString();
                    }
                },
                marker: { show: true }
            },
            colors: [color],
            markers: {
                size: 0,
                colors: [color],
                strokeColors: '#fff',
                strokeWidth: 2,
                discrete: markers
            },
            annotations: showLatestValue && chartLabels.length > 0 ? {
                points: [{
                    x: chartLabels[chartLabels.length - 1],
                    y: latestValue,
                    marker: {
                        size: 4,
                        fillColor: '#fff',
                        strokeColor: color,
                        strokeWidth: 2,
                        radius: 2
                    },
                    label: {
                        borderColor: '#fff',
                        offsetY: 0,
                        style: {
                            color: '#fff',
                            background: color,
                            fontSize: '10px',
                            fontWeight: 'bold',
                            padding: {
                                left: 5,
                                right: 5,
                                top: 2,
                                bottom: 2
                            },
                            borderRadius: 3
                        },
                        text: latestValue.toLocaleString()
                    }
                }]
            } : {}
        };
    }

    // Configure all charts
    const chartConfigs = [
        {
            selector: "#sparkline-clients",
            data: chartClientsData,
            color: '#198754',
            name: "Clients"
        },
        {
            selector: "#sparkline-title-deeds",
            data: chartTitleDeedsData,
            color: '#0dcaf0',
            name: "Title Deeds"
        },
        {
            selector: "#sparkline-revenue",
            data: chartRevenueData,
            color: '#007bff',
            name: "Revenue"
        },
        {
            selector: "#sparkline-expenses",
            data: chartExpensesData,
            color: '#dc3545',
            name: "Expenses"
        },
        {
            selector: "#sparkline-net-revenue",
            data: chartNetRevenueData,
            color: '#ffc107',
            name: "Net Revenue"
        }
    ];

    // Render all charts
    chartConfigs.forEach(config => {
        try {
            const chartElement = document.querySelector(config.selector);

            if (chartElement) {
                const options = createChartConfig(
                    config.selector,
                    config.data,
                    config.color,
                    config.name
                );

                const chart = new ApexCharts(chartElement, options);
                chart.render();
            } else {
                console.warn(`Element not found: ${config.selector}`);
            }
        } catch (err) {
            console.error(`Error rendering chart ${config.selector}:`, err);
        }
    });
});