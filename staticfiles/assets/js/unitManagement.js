// unitManagement.js
$(function () {
    // Open modal
    $(document).on("click", ".open-unit-modal", function (e) {
        e.preventDefault();
        const url = $(this).attr("href");
        $("#unitModal .modal-content").load(url, function () {
            $("#unitModal").modal("show");
        });
    });

    // Submit form via AJAX
    $(document).on("submit", "#unitModal form", function (e) {
        e.preventDefault();
        const form = $(this);
        const url = form.attr("action");
        const method = form.attr("method");

        $.ajax({
            url: url,
            type: method,
            data: form.serialize(),
            headers: { "X-Requested-With": "XMLHttpRequest" },
            success: function (data) {
                if (data.success) {
                    $("#unitModal").modal("hide");
                    toastr.success(data.message || "Success");
                    if (data.redirect_url) {
                        window.location.href = data.redirect_url;
                    } else {
                        location.reload();
                    }
                } else {
                    // Replace modal content with new form HTML if validation errors
                    $("#unitModal .modal-content").html(data.html);
                }
            },
            error: function () {
                toastr.error("An error occurred while saving the unit.");
            }
        });
    });

    // Delete unit via AJAX
    $(document).on("submit", "#unitDeleteForm", function (e) {
        e.preventDefault();
        const form = $(this);
        $.ajax({
            url: form.attr("action"),
            type: "POST",
            data: form.serialize(),
            headers: { "X-Requested-With": "XMLHttpRequest" },
            success: function (data) {
                if (data.success) {
                    $("#unitModal").modal("hide");
                    toastr.success(data.message || "Unit deleted successfully");
                    location.reload();
                } else {
                    toastr.error("Could not delete unit.");
                }
            }
        });
    });
});
