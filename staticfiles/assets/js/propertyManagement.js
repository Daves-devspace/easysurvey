function openCreatePropertyModal() {
  fetch("/property-management/properties/create/")
    .then((res) => res.text())
    .then((html) => {
      document.getElementById("propertyModalContent").innerHTML = html;
      const modal = new bootstrap.Modal(document.getElementById("propertyModal"));
      modal.show();
    });
}

function openEditPropertyModal(id) {
  fetch(`/property-management/properties/${id}/edit/`)  // backticks ✅
    .then((res) => res.text())
    .then((html) => {
      document.getElementById("propertyModalContent").innerHTML = html;
      const modal = new bootstrap.Modal(document.getElementById("propertyModal"));
      modal.show();
    });
}

function openDeletePropertyModal(id) {
  fetch(`/property-management/delete/${id}/`)  // backticks ✅
    .then((res) => res.text())
    .then((html) => {
      document.getElementById("propertyModalContent").innerHTML = html;
      const modal = new bootstrap.Modal(document.getElementById("propertyModal"));
      modal.show();
    });
}
