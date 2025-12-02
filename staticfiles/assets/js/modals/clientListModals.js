// Wait for full document load
document.addEventListener("DOMContentLoaded", () => {
    // Form elements
    const categorySelect = document.querySelector("#id_category");
    const serviceSelect = document.querySelector("#id_service");
    const processSection = document.getElementById("processCostSection");
    const processTableBody = document.getElementById("processTableBody");
    const totalCostDisplay = document.getElementById("totalCost");
    const overridePriceInput = document.getElementById("overrideTotalPrice");
    const overridePriceSection = document.getElementById("totalPriceOverride");
    const groundFields = document.getElementById("groundFields");
    const clientNameInput = document.getElementById("clientName");
    const scheduledDateInput = document.getElementById("scheduledDate");
    const dispatchMessageInput = document.getElementById("dispatchMessage");

    // Format date nicely for dispatch message
    const formatDate = (date) => {
        return date.toLocaleDateString(undefined, {
            weekday: 'long',
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    };

    // Automatically generate dispatch message
    const updateDispatchMessage = () => {
        const clientName = clientNameInput.value || "client";
        const dateValue = scheduledDateInput.value;
        if (dateValue) {
            const date = new Date(dateValue);
            const message = `Dispatching officer to ${clientName}'s site on ${formatDate(date)} at 10 AM.`;
            dispatchMessageInput.value = message;
        }
    };

    // Update message on date change
    scheduledDateInput?.addEventListener("change", updateDispatchMessage);

    // Handle category selection
    categorySelect?.addEventListener("change", () => {
        const category = categorySelect.value;
        if (!category) return;

        serviceSelect.innerHTML = '<option value="">Loading...</option>';

        fetch(`/services/by-category/?category=${category}`)
            .then(res => res.json())
            .then(data => {
                serviceSelect.innerHTML = '<option value="">Select Service</option>';
                data.services.forEach(service => {
                    serviceSelect.innerHTML += `<option value="${service.id}">${service.name}</option>`;
                });
            });
    });

    // Handle service selection
    serviceSelect?.addEventListener("change", () => {
        const serviceId = serviceSelect.value;
        const selectedCategory = categorySelect.value.toLowerCase();
        if (!serviceId) return;

        fetch(`/get_service_processes/${serviceId}/`)
            .then(res => res.json())
            .then(data => {
                if (selectedCategory === "ground") {
                    groundFields.style.display = "block";

                    const clientName = clientNameInput.value || "client";
                    const today = new Date();
                    const tomorrow = new Date();
                    tomorrow.setDate(today.getDate() + 1);
                    scheduledDateInput.valueAsDate = tomorrow;

                    const message = `Dispatching officer to ${clientName}'s site on ${formatDate(tomorrow)} at 10 AM.`;
                    dispatchMessageInput.value = message;
                } else {
                    groundFields.style.display = "none";
                }

                processTableBody.innerHTML = '';
                let total = 0;

                if (data.processes.length === 0) {
                    processSection.style.display = "none";
                    overridePriceSection.style.display = "block";
                    overridePriceInput.value = data.total_price;
                } else {
                    overridePriceSection.style.display = "none";
                    processSection.style.display = "block";

                    data.processes.forEach(process => {
                        total += process.default_cost;

                        processTableBody.innerHTML += `
                            <tr>
                                <td>
                                    ${process.name}
                                    <input type="hidden" name="process_id[]" value="${process.id}">
                                    <input type="hidden" name="process_name[]" value="${process.name}">
                                </td>
                                <td>
                                    <input type="number"
                                        name="process_cost[]"
                                        value="${process.default_cost}"
                                        step="0.01"
                                        class="form-control cost-input">
                                </td>
                            </tr>
                        `;
                    });

                    totalCostDisplay.innerText = total.toFixed(2);

                    document.querySelectorAll(".cost-input").forEach(input => {
                        input.addEventListener("input", () => {
                            let newTotal = 0;
                            document.querySelectorAll(".cost-input").forEach(inp => {
                                newTotal += parseFloat(inp.value || 0);
                            });
                            totalCostDisplay.innerText = newTotal.toFixed(2);
                        });
                    });
                }
            });
    });
});

// jQuery client search interactions
$(document).ready(function () {
    // Search client dynamically
    $('#clientSearch').on('input', function () {
        const query = $(this).val().trim();
        if (query.length > 1) {
            $.ajax({
                url: '/clients/search/',
                data: { term: query },
                success: function (data) {
                    $('#clientList').empty();
                    if (data.results.length > 0) {
                        data.results.forEach(function (client) {
                            $('#clientList').append(`
                                <li class="list-group-item" data-id="${client.id}" data-name="${client.first_name} ${client.last_name}" data-phone="${client.phone}">
                                    ${client.first_name} ${client.last_name} - ${client.phone}
                                </li>
                            `);
                        });
                    } else {
                        $('#clientList').append('<li class="list-group-item">No results found</li>');
                    }
                }
            });
        } else {
            $('#clientList').empty();
        }
    });

    // Select a client from the search result
    $('#clientList').on('click', 'li', function () {
        const clientId = $(this).data('id');
        const clientName = $(this).data('name');
        const clientPhone = $(this).data('phone');

        $('#hiddenClientId').val(clientId);
        $('#clientName').val(clientName);
        $('#clientPhone').val(clientPhone);

        $('#clientList').empty(); // clear suggestions
    });
});
