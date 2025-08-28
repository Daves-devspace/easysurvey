// // tenants/static/tenants/js/tenant_management.js

// // Open modals with form content
// function openCreateTenantModal(propertyId) {
//   fetch(`/tenant_management/${propertyId}/create/`)
//     .then((res) => res.text())
//     .then((html) => {
//       document.getElementById("tenantModalContent").innerHTML = html;
//       const modal = new bootstrap.Modal(document.getElementById("tenantModal"));
//       modal.show();
//       initFormHandlers('create');
//     });
// }

// function openEditTenantModal(propertyId, tenantId) {
//   fetch(`/tenant_management/${propertyId}/edit/${tenantId}/`)
//     .then((res) => res.text())
//     .then((html) => {
//       document.getElementById("tenantModalContent").innerHTML = html;
//       const modal = new bootstrap.Modal(document.getElementById("tenantModal"));
//       modal.show();
//       initFormHandlers('edit');
//     });
// }

// function openDeleteTenantModal(propertyId, tenantId) {
//   fetch(`/tenant_management/${propertyId}/delete/${tenantId}/`)
//     .then((res) => res.text())
//     .then((html) => {
//       document.getElementById("tenantModalContent").innerHTML = html;
//       const modal = new bootstrap.Modal(document.getElementById("tenantModal"));
//       modal.show();
//       initFormHandlers('delete');
//     });
// }

// // Initialize form submission handlers
// function initFormHandlers(action) {
//   const form = document.getElementById('tenantForm');
//   const deleteForm = document.getElementById('deleteForm');
  
//   if (form) {
//     form.onsubmit = function(e) {
//       e.preventDefault();
//       submitForm(this, action);
//     };
//   }
  
//   if (deleteForm) {
//     deleteForm.onsubmit = function(e) {
//       e.preventDefault();
//       submitDeleteForm(this);
//     };
//   }
// }

// // Handle form submission via AJAX
// function submitForm(form, action) {
//   const formData = new FormData(form);
//   const url = form.action;
//   const propertyId = form.dataset.propertyId;
//   const tenantId = form.dataset.tenantId || '';
  
//   fetch(url, {
//     method: 'POST',
//     body: formData,
//     headers: {
//       'X-Requested-With': 'XMLHttpRequest',
//     },
//   })
//   .then(response => response.json())
//   .then(data => {
//     if (data.success) {
//       // Close modal
//       const modal = bootstrap.Modal.getInstance(document.getElementById('tenantModal'));
//       modal.hide();
      
//       // Show success message
//       showMessage(data.message || 'Operation completed successfully', 'success');
      
//       // Update UI without full page reload
//       if (action === 'create') {
//         addTenantToTable(data.tenant);
//       } else if (action === 'edit') {
//         updateTenantInTable(data.tenant);
//       }
//     } else {
//       // Show form with validation errors
//       document.getElementById('tenantModalContent').innerHTML = data.form_html;
//       initFormHandlers(action);
//     }
//   })
//   .catch(error => {
//     showMessage('An error occurred. Please try again.', 'error');
//     console.error('Error:', error);
//   });
// }

// // Handle delete form submission
// function submitDeleteForm(form) {
//   const formData = new FormData(form);
//   const url = form.action;
  
//   fetch(url, {
//     method: 'POST',
//     body: formData,
//     headers: {
//       'X-Requested-With': 'XMLHttpRequest',
//     },
//   })
//   .then(response => response.json())
//   .then(data => {
//     if (data.success) {
//       // Close modal
//       const modal = bootstrap.Modal.getInstance(document.getElementById('tenantModal'));
//       modal.hide();
      
//       // Show success message
//       showMessage(data.message || 'Tenant deleted successfully', 'success');
      
//       // Remove from UI
//       removeTenantFromTable(data.tenant_id);
//     } else {
//       showMessage(data.error || 'Failed to delete tenant', 'error');
//     }
//   })
//   .catch(error => {
//     showMessage('An error occurred. Please try again.', 'error');
//     console.error('Error:', error);
//   });
// }

// // UI update functions
// function addTenantToTable(tenantData) {
//   const tbody = document.querySelector('#tenantsTable tbody');
//   const newRow = document.createElement('tr');
//   newRow.id = `tenant-row-${tenantData.id}`;
//   newRow.innerHTML = `
//     <td>${tenantData.full_name}</td>
//     <td>${tenantData.phone_number}</td>
//     <td>${tenantData.email || '-'}</td>
//     <td>${tenantData.unit_number || '-'}</td>
//     <td>
//       <button class="btn btn-sm btn-outline-primary" 
//               onclick="openEditTenantModal(${tenantData.property_id}, ${tenantData.id})">
//         Edit
//       </button>
//       <button class="btn btn-sm btn-outline-danger" 
//               onclick="openDeleteTenantModal(${tenantData.property_id}, ${tenantData.id})">
//         Delete
//       </button>
//     </td>
//   `;
//   tbody.appendChild(newRow);
// }

// function updateTenantInTable(tenantData) {
//   const row = document.getElementById(`tenant-row-${tenantData.id}`);
//   if (row) {
//     row.innerHTML = `
//       <td>${tenantData.full_name}</td>
//       <td>${tenantData.phone_number}</td>
//       <td>${tenantData.email || '-'}</td>
//       <td>${tenantData.unit_number || '-'}</td>
//       <td>
//         <button class="btn btn-sm btn-outline-primary" 
//                 onclick="openEditTenantModal(${tenantData.property_id}, ${tenantData.id})">
//           Edit
//         </button>
//         <button class="btn btn-sm btn-outline-danger" 
//                 onclick="openDeleteTenantModal(${tenantData.property_id}, ${tenantData.id})">
//           Delete
//         </button>
//       </td>
//     `;
//   }
// }

// function removeTenantFromTable(tenantId) {
//   const row = document.getElementById(`tenant-row-${tenantId}`);
//   if (row) {
//     row.remove();
//   }
// }

// // Show toast messages
// function showMessage(message, type) {
//   // Create toast element if it doesn't exist
//   if (!document.getElementById('messageToast')) {
//     const toastContainer = document.createElement('div');
//     toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
//     toastContainer.innerHTML = `
//       <div id="messageToast" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
//         <div class="toast-header">
//           <strong class="me-auto">Notification</strong>
//           <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
//         </div>
//         <div class="toast-body"></div>
//       </div>
//     `;
//     document.body.appendChild(toastContainer);
//   }
  
//   // Set message and style
//   const toast = document.getElementById('messageToast');
//   const toastBody = toast.querySelector('.toast-body');
//   toastBody.textContent = message;
  
//   // Add appropriate styling
//   toast.classList.remove('bg-success', 'bg-danger', 'bg-warning');
//   if (type === 'success') {
//     toast.classList.add('bg-success', 'text-white');
//   } else if (type === 'error') {
//     toast.classList.add('bg-danger', 'text-white');
//   } else {
//     toast.classList.add('bg-warning');
//   }
  
//   // Show the toast
//   const bsToast = new bootstrap.Toast(toast);
//   bsToast.show();
// }

// // Initialize when document is ready
// document.addEventListener('DOMContentLoaded', function() {
//   // Add event listeners to any existing forms on page load
//   const form = document.getElementById('tenantForm');
//   if (form) {
//     form.onsubmit = function(e) {
//       e.preventDefault();
//       submitForm(this, 'edit');
//     };
//   }
// });