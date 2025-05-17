document.addEventListener('DOMContentLoaded', function () {
    const editForms = document.querySelectorAll('.edit-client-form');

    editForms.forEach(form => {
        form.addEventListener('submit', function (e) {
            e.preventDefault();

            const url = this.action;
            const formData = new FormData(this);

            fetch(url, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': formData.get('csrfmiddlewaretoken')
                },
                body: formData
            })
                .then(response => response.json())
                .then(data => {
                    if (data.message) {
                        alert(data.message);
                        window.location.reload();  // refresh page to reflect changes
                    } else if (data.errors) {
                        alert('Error: ' + JSON.stringify(data.errors));
                    }
                })
                .catch(error => {
                    console.error('AJAX Error:', error);
                });
        });
    });
});

