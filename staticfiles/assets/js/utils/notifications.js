document.addEventListener("DOMContentLoaded", function () {
  const listGroup = document.querySelector(".notification-list");
  const badge = document.querySelector(".notification-badge");
  const markAllReadBtn = document.getElementById("markAllRead");

  // Store user context
  let currentUser = {
    is_superuser: false,
    username: "",
    id: null,
  };

  // ✅ Get CSRF token
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + "=") {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  const csrftoken = getCookie("csrftoken");

  // --- WebSocket Setup (optional, gracefully fails if not configured)
  const wsProtocol = window.location.protocol === "https:" ? "wss://" : "ws://";
  let ws = null;
  let reconnectAttempts = 0;
  const maxReconnectAttempts = 3; // Reduced from 5
  let wsEnabled = true; // Flag to disable WS if not configured

  function connectWebSocket() {
    if (!wsEnabled) {
      console.log("ℹ️ WebSocket disabled (not configured on server)");
      return;
    }

    try {
      ws = new WebSocket(
        wsProtocol + window.location.host + "/ws/notifications/"
      );

      ws.onopen = function () {
        console.log("✅ WebSocket connected");
        reconnectAttempts = 0;
      };

      ws.onerror = function (error) {
        console.warn(
          "⚠️ WebSocket error (this is okay if Channels isn't configured)"
        );
      };

      ws.onclose = function (event) {
        console.log("🔌 WebSocket disconnected");

        // If initial connection failed (code 1006), disable WebSocket
        if (reconnectAttempts === 0 && event.code === 1006) {
          console.log(
            "ℹ️ WebSocket not available on server - real-time updates disabled"
          );
          wsEnabled = false;
          return;
        }

        // Try to reconnect with exponential backoff
        if (reconnectAttempts < maxReconnectAttempts && wsEnabled) {
          reconnectAttempts++;
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 10000);
          console.log(
            `🔄 Reconnecting in ${
              delay / 1000
            }s... (attempt ${reconnectAttempts}/${maxReconnectAttempts})`
          );
          setTimeout(connectWebSocket, delay);
        } else {
          console.log("ℹ️ WebSocket disabled - using polling instead");
          wsEnabled = false;
        }
      };

      ws.onmessage = function (e) {
        const data = JSON.parse(e.data);
        addNotificationToList(data);
      };
    } catch (error) {
      console.warn(
        "⚠️ Failed to create WebSocket (Channels not configured):",
        error.message
      );
      wsEnabled = false;
    }
  }

  // Connect WebSocket (will fail gracefully if not configured)
  connectWebSocket();

  /**
   * Fetch and merge notifications from both feeds
   */
  async function fetchNotifications() {
    try {
      const res = await fetch("/notifications/api/notifications/");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      const { personal, monitoring, user } = data;

      // Store user context
      if (user) {
        currentUser = user;
        console.log(
          "👤 Current user:",
          currentUser.username,
          currentUser.is_superuser ? "(Superuser)" : "(Regular)"
        );
      }

      // Merge both feeds with proper flags
      // Mark notifications from monitoring feed as isMonitored=true
      const allNotifications = [
        ...personal.map((n) => ({
          ...n,
          isMonitored: false,
          _source: "personal", // Debug flag
        })),
        ...(monitoring || []).map((n) => ({
          ...n,
          isMonitored: true,
          _source: "monitoring", // Debug flag
        })),
      ];

      console.log("📦 Merged notifications:", {
        personal: personal.length,
        monitoring: monitoring ? monitoring.length : 0,
        total: allNotifications.length,
      });

      // Sort by created_at (newest first)
      allNotifications.sort(
        (a, b) => new Date(b.created_at) - new Date(a.created_at)
      );

      renderNotifications(allNotifications);
      updateBadge(personal.length);
    } catch (err) {
      console.error("Error loading notifications:", err);
      listGroup.innerHTML = `<p class="text-center text-danger py-2 m-0">Failed to load notifications</p>`;
    }
  }

  /**
   * Render unified notification list
   */
  function renderNotifications(notifications) {
    listGroup.innerHTML = "";

    if (!notifications || notifications.length === 0) {
      listGroup.innerHTML = `
        <div class="text-center py-4">
          <i class="ti ti-bell-off" style="font-size: 3rem; color: #d1d5db;"></i>
          <p class="text-muted mt-2 mb-0">No notifications</p>
        </div>
      `;
      return;
    }

    notifications.forEach((n) => {
      const item = createNotificationItem(n);
      listGroup.appendChild(item);
    });
  }

  /**
   * Create a single notification item with visual flags
   */
  function createNotificationItem(notification) {
    const item = document.createElement("a");
    item.className = "list-group-item list-group-item-action notification-item";
    item.dataset.id = notification.id;
    // ✅ Store as string to avoid type coercion issues
    item.dataset.monitored = String(notification.isMonitored);

    // Debug log
    console.log(`🏗️ Creating notification ${notification.id}:`, {
      isMonitored: notification.isMonitored,
      source: notification._source,
      datasetMonitored: item.dataset.monitored,
    });

    const time = new Date(notification.created_at).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const date = new Date(notification.created_at).toLocaleDateString([], {
      month: "short",
      day: "numeric",
    });
    const username = notification.target_user || "Unknown";

    // Determine notification type icon and color
    let typeIcon = "ti-bell";
    let typeColor = "#6366f1"; // Default indigo

    if (notification.title.toLowerCase().includes("survey")) {
      typeIcon = "ti-map-pin";
      typeColor = "#10b981"; // Green
    } else if (notification.title.toLowerCase().includes("booking")) {
      typeIcon = "ti-calendar";
      typeColor = "#f59e0b"; // Amber
    } else if (notification.title.toLowerCase().includes("payment")) {
      typeIcon = "ti-credit-card";
      typeColor = "#3b82f6"; // Blue
    } else if (notification.title.toLowerCase().includes("document")) {
      typeIcon = "ti-file";
      typeColor = "#8b5cf6"; // Purple
    }

    item.innerHTML = `
      <div class="notification-wrapper">
        <!-- Left: Icon with type indicator -->
        <div class="notification-icon" style="background-color: ${typeColor}15;">
          <i class="ti ${typeIcon}" style="color: ${typeColor};"></i>
        </div>

        <!-- Center: Content -->
        <div class="notification-content">
          <div class="notification-header">
            <span class="notification-title">${notification.title}</span>
            <span class="notification-time">${time}</span>
          </div>
          <p class="notification-message">${notification.message}</p>
          
          <!-- Flags Row -->
          <div class="notification-flags">
            ${
              notification.isMonitored
                ? `
              <span class="flag flag-monitored">
                <i class="ti ti-eye"></i>
                <span>${username}</span>
              </span>
            `
                : ""
            }
            <span class="flag flag-date">
              <i class="ti ti-clock"></i>
              <span>${date}</span>
            </span>
          </div>
        </div>

        <!-- Right: Status indicator -->
        <div class="notification-status">
          <span class="status-dot ${
            notification.isMonitored ? "monitored" : "personal"
          }"></span>
        </div>
      </div>
    `;

    item.addEventListener("click", (e) => {
      e.preventDefault();
      const isMonitored = item.dataset.monitored === "true";
      console.log(
        `📌 Marking notification ${notification.id} as read (monitored: ${isMonitored})`
      );
      markAsRead(notification.id, item, isMonitored);
    });

    return item;
  }

  /**
   * Mark single notification as read
   */
  async function markAsRead(id, element, isMonitored) {
    try {
      console.log(`🔄 Sending mark-as-read request for notification ${id}...`);

      const res = await fetch(
        `/notifications/api/notifications/${id}/mark-read/`,
        {
          method: "POST",
          headers: {
            "X-CSRFToken": csrftoken,
            "Content-Type": "application/json",
          },
        }
      );

      if (!res.ok) {
        const errorText = await res.text();
        console.error("Failed to mark as read:", res.status, errorText);
        alert("Failed to mark notification as read. Please try again.");
        return;
      }

      const result = await res.json();
      console.log("✅ Mark-as-read response:", result);

      // Fade out animation
      element.style.transition = "all 0.3s ease";
      element.style.opacity = "0";
      element.style.transform = "translateX(20px)";

      setTimeout(() => {
        element.remove();

        // Only decrement badge for personal notifications
        if (!isMonitored) {
          decrementBadge();
        }

        // Check if list is empty
        const items = listGroup.querySelectorAll(".notification-item");
        if (items.length === 0) {
          listGroup.innerHTML = `
            <div class="text-center py-4">
              <i class="ti ti-bell-off" style="font-size: 3rem; color: #d1d5db;"></i>
              <p class="text-muted mt-2 mb-0">No notifications</p>
            </div>
          `;
        }
      }, 300);
    } catch (err) {
      console.error("Error marking as read:", err);
      alert("Network error. Please check your connection.");
    }
  }

  /**
   * Mark all as read
   */
  markAllReadBtn.addEventListener("click", async () => {
    if (!confirm("Mark all notifications as read?")) return;

    try {
      console.log("🔄 Marking all notifications as read...");

      const res = await fetch(
        "/notifications/api/notifications/mark-all-read/",
        {
          method: "POST",
          headers: {
            "X-CSRFToken": csrftoken,
            "Content-Type": "application/json",
          },
        }
      );

      if (!res.ok) {
        const errorText = await res.text();
        console.error("Failed to mark all as read:", res.status, errorText);
        alert("Failed to mark all notifications as read. Please try again.");
        return;
      }

      const result = await res.json();
      console.log("✅ Mark-all-as-read response:", result);

      listGroup.innerHTML = `
        <div class="text-center py-4">
          <i class="ti ti-check-circle" style="font-size: 3rem; color: #10b981;"></i>
          <p class="text-success mt-2 mb-0">All notifications marked as read</p>
        </div>
      `;
      updateBadge(0);
    } catch (err) {
      console.error("Error marking all as read:", err);
      alert("Network error. Please check your connection.");
    }
  });

  /**
   * Badge updates
   */
  function updateBadge(count) {
    badge.textContent = count > 0 ? count : "";
    badge.style.display = count > 0 ? "inline-block" : "none";
  }

  function decrementBadge() {
    const current = parseInt(badge.textContent || "0");
    updateBadge(Math.max(current - 1, 0));
  }

  function incrementBadge() {
    const current = parseInt(badge.textContent || "0");
    updateBadge(current + 1);
  }

  function addNotificationToList(notification) {
    // Check if empty state is showing
    const emptyState = listGroup.querySelector(".text-center");
    if (emptyState) {
      listGroup.innerHTML = "";
    }

    const item = createNotificationItem({
      ...notification,
      isMonitored: false,
    });

    // Slide in animation
    item.style.opacity = "0";
    item.style.transform = "translateY(-20px)";

    listGroup.prepend(item);

    // Trigger animation
    setTimeout(() => {
      item.style.transition = "all 0.4s ease";
      item.style.opacity = "1";
      item.style.transform = "translateY(0)";
    }, 10);

    incrementBadge();
  }

  // Initial fetch
  fetchNotifications();
});

/* ============================================
   CSS STYLES - Add to your stylesheet
   ============================================ */

const styles = `
<style>
/* Notification Item */
.notification-item {
  border: none;
  border-bottom: 1px solid #e5e7eb;
  padding: 0;
  transition: all 0.2s ease;
  cursor: pointer;
  background: #fff;
}

.notification-item:hover {
  background-color: #f9fafb;
  transform: translateX(4px);
}

.notification-item:last-child {
  border-bottom: none;
}

/* Notification Wrapper */
.notification-wrapper {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 16px;
}

/* Icon Container */
.notification-icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.25rem;
}

/* Content Area */
.notification-content {
  flex: 1;
  min-width: 0;
}

.notification-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
  gap: 8px;
}

.notification-title {
  font-weight: 600;
  font-size: 0.9rem;
  color: #111827;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.notification-time {
  font-size: 0.75rem;
  color: #9ca3af;
  white-space: nowrap;
}

.notification-message {
  font-size: 0.85rem;
  color: #6b7280;
  margin: 0 0 8px 0;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Flags */
.notification-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.flag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 0.75rem;
  font-weight: 500;
}

.flag i {
  font-size: 0.85rem;
}

.flag-monitored {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: #fff;
  box-shadow: 0 2px 4px rgba(59, 130, 246, 0.2);
}

.flag-date {
  background: #f3f4f6;
  color: #6b7280;
}

/* Status Dot */
.notification-status {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.status-dot.personal {
  background: #10b981;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2);
}

.status-dot.monitored {
  background: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* Empty State */
.notification-list:empty::after {
  content: "";
  display: block;
  text-align: center;
  padding: 2rem;
  color: #9ca3af;
}

/* Responsive */
@media (max-width: 576px) {
  .notification-wrapper {
    gap: 10px;
    padding: 10px 12px;
  }

  .notification-icon {
    width: 36px;
    height: 36px;
    font-size: 1.1rem;
  }

  .notification-title {
    font-size: 0.85rem;
  }

  .notification-message {
    font-size: 0.8rem;
  }

  .notification-time {
    font-size: 0.7rem;
  }

  .flag {
    padding: 2px 6px;
    font-size: 0.7rem;
  }

  .status-dot {
    width: 6px;
    height: 6px;
  }
}

/* Scrollbar */
.notification-list {
  max-height: 500px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #d1d5db #f3f4f6;
}

.notification-list::-webkit-scrollbar {
  width: 6px;
}

.notification-list::-webkit-scrollbar-track {
  background: #f3f4f6;
  border-radius: 10px;
}

.notification-list::-webkit-scrollbar-thumb {
  background: #d1d5db;
  border-radius: 10px;
}

.notification-list::-webkit-scrollbar-thumb:hover {
  background: #9ca3af;
}
</style>
`;

// Inject styles into the document
if (!document.getElementById("notification-styles")) {
  const styleSheet = document.createElement("div");
  styleSheet.id = "notification-styles";
  styleSheet.innerHTML = styles;
  document.head.appendChild(styleSheet);
}
