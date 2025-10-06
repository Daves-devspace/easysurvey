// static/js/bot.js
/**
 * EasyDocs Chatbot - Refined Version
 * Features: Clean typing indicator, cache management, online status
 */

document.addEventListener("DOMContentLoaded", () => {
  // DOM Elements
  const chatButton = document.getElementById("chatBotButton");
  const chatSidebar = document.getElementById("chatBotSidebar");
  const chatClose = document.getElementById("chatBotClose");
  const chatMessages = document.getElementById("chatBotMessages");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");
  const clearChatBtn = document.getElementById("clearChatBtn");
  const clearCacheBtn = document.getElementById("clearCacheBtn");

  // Configuration
  const API_URL = "/api/bot/query/";
  const CLEAR_SESSION_URL = "/api/bot/clear-session/";
  const HEALTH_URL = "/api/bot/health/";
  const username = (window.currentUser || "guest").trim();

  // State
  let conversationHistory = [];
  let isProcessing = false;

  // ============================================================================
  // Utility Functions
  // ============================================================================

  function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function formatTimestamp() {
    const now = new Date();
    return now.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

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

  // ============================================================================
  // Message Rendering
  // ============================================================================

  function createMessage(text, type = "bot", options = {}) {
    const wrapper = document.createElement("div");
    wrapper.classList.add("message-wrapper", `${type}-message-wrapper`);

    const messageDiv = document.createElement("div");
    messageDiv.classList.add(`${type}-message`);

    // Format bot messages (support bold, italic, line breaks)
    if (type === "bot") {
      let formatted = text
        .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.*?)\*/g, "<em>$1</em>")
        .replace(/\n/g, "<br>")
        .replace(/•/g, "&bull;");
      messageDiv.innerHTML = formatted;
    } else {
      messageDiv.textContent = text;
    }

    // Add confidence badge for bot messages
    if (type === "bot" && options.confidence !== undefined) {
      const badge = document.createElement("span");
      badge.classList.add("confidence-badge");
      const percentage = Math.round(options.confidence * 100);
      badge.textContent = `${percentage}%`;

      if (percentage >= 80) badge.classList.add("high");
      else if (percentage >= 50) badge.classList.add("medium");
      else badge.classList.add("low");

      messageDiv.appendChild(badge);
    }

    wrapper.appendChild(messageDiv);

    // Add timestamp
    const timestamp = document.createElement("span");
    timestamp.classList.add("message-timestamp");
    timestamp.textContent = formatTimestamp();
    wrapper.appendChild(timestamp);

    chatMessages.appendChild(wrapper);
    scrollToBottom();

    return messageDiv;
  }

  // ============================================================================
  // Typing Indicator
  // ============================================================================

  let typingElement = null;

  function showTyping() {
    if (!typingElement) {
      const wrapper = document.createElement("div");
      wrapper.classList.add("message-wrapper", "bot-message-wrapper");
      wrapper.id = "typingIndicator";

      const indicator = document.createElement("div");
      indicator.classList.add("typing-indicator");

      const dots = document.createElement("div");
      dots.classList.add("typing-dots");
      dots.innerHTML = "<span></span><span></span><span></span>";

      indicator.appendChild(dots);
      wrapper.appendChild(indicator);

      chatMessages.appendChild(wrapper);
      scrollToBottom();

      typingElement = wrapper;
    }
  }

  function hideTyping() {
    if (typingElement) {
      typingElement.remove();
      typingElement = null;
    }
  }

  // ============================================================================
  // Local Storage - Conversation History
  // ============================================================================

  const STORAGE_KEY = `easydocs_chat_${username}`;
  const MAX_HISTORY = 50;

  function saveToHistory(message) {
    conversationHistory.push(message);

    if (conversationHistory.length > MAX_HISTORY) {
      conversationHistory = conversationHistory.slice(-MAX_HISTORY);
    }

    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversationHistory));
    } catch (e) {
      console.warn("Failed to save history:", e);
    }
  }

  function loadHistory() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        conversationHistory = JSON.parse(saved);
        renderHistory();
      }
    } catch (e) {
      console.warn("Failed to load history:", e);
      conversationHistory = [];
    }
  }

  function renderHistory() {
    chatMessages.innerHTML = "";

    conversationHistory.forEach((msg) => {
      createMessage(msg.text, msg.type, {
        confidence: msg.confidence,
      });
    });

    scrollToBottom();
  }

  function clearHistory() {
    if (confirm("Clear all chat history? This cannot be undone.")) {
      conversationHistory = [];
      chatMessages.innerHTML = "";

      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch (e) {
        console.warn("Failed to clear storage:", e);
      }

      // Clear server-side session
      clearServerSession();

      // Show welcome message
      showWelcome();
    }
  }

  // ============================================================================
  // API Functions
  // ============================================================================

  async function sendToBot(userText) {
    if (isProcessing) return;
    isProcessing = true;

    // Disable send button
    chatSend.disabled = true;

    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        body: JSON.stringify({
          text: userText,
          username: username,
        }),
      });

      const data = await response.json();

      hideTyping();

      if (data.ok && data.answer) {
        // Show bot response
        createMessage(data.answer, "bot", {
          confidence: data.confidence,
        });

        // Save to history
        saveToHistory({
          text: data.answer,
          type: "bot",
          confidence: data.confidence,
          timestamp: Date.now(),
        });

        // Log performance
        console.log(
          `Bot: ${data.method} | ${
            data.cached ? "CACHED" : "FRESH"
          } | ${Math.round(data.confidence * 100)}%`
        );
      } else {
        // Error
        createMessage(
          data.answer || "Sorry, I encountered an error. Please try again.",
          "bot"
        );
      }
    } catch (error) {
      console.error("API Error:", error);
      hideTyping();
      createMessage(
        "Network error. Please check your connection and try again.",
        "bot"
      );
    } finally {
      isProcessing = false;
      chatSend.disabled = false;
      chatInput.focus();
    }
  }

  async function clearServerSession() {
    try {
      await fetch(CLEAR_SESSION_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        body: JSON.stringify({ username }),
      });
      console.log("Server session cleared");
    } catch (e) {
      console.warn("Failed to clear server session:", e);
    }
  }

  async function clearCache() {
    if (!confirm("Clear bot cache? This will refresh all cached responses.")) {
      return;
    }

    const btn = clearCacheBtn;
    const originalHTML = btn.innerHTML;

    // Show loading
    btn.classList.add("loading");
    btn.disabled = true;

    try {
      // Call Django cache clear (you need to add this endpoint)
      await fetch("/api/bot/clear-cache/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
      });

      // Show success message
      const successMsg = createMessage(
        "Cache cleared successfully! Responses will be fresh.",
        "bot",
        { confidence: 1.0 }
      );

      setTimeout(() => {
        btn.classList.remove("loading");
        btn.disabled = false;
      }, 1000);
    } catch (error) {
      console.error("Cache clear error:", error);
      createMessage("Failed to clear cache. Please try again.", "bot");

      btn.classList.remove("loading");
      btn.disabled = false;
    }
  }

  // ============================================================================
  // Message Handling
  // ============================================================================

  function sendMessage() {
    const userText = chatInput.value.trim();

    if (!userText || isProcessing) return;

    // Clear input
    chatInput.value = "";
    chatInput.focus();

    // Show user message
    createMessage(userText, "user");

    // Save to history
    saveToHistory({
      text: userText,
      type: "user",
      timestamp: Date.now(),
    });

    // Show typing indicator
    showTyping();

    // Send to bot
    sendToBot(userText);
  }

  // ============================================================================
  // Welcome Message
  // ============================================================================

  function showWelcome() {
    const welcomeText =
      username !== "guest"
        ? `Hi ${username}! How can I help you today?`
        : `Hi! How can I help you today?`;

    createMessage(welcomeText, "bot", { confidence: 1.0 });

    saveToHistory({
      text: welcomeText,
      type: "bot",
      confidence: 1.0,
      timestamp: Date.now(),
    });
  }

  // ============================================================================
  // Online Status Check
  // ============================================================================

  async function checkBotStatus() {
    const statusIndicator = document.querySelector(".status-indicator");
    const statusText = document.querySelector(".status-text");

    try {
      const response = await fetch(HEALTH_URL);
      const data = await response.json();

      if (data.status === "healthy") {
        statusIndicator.classList.add("online");
        statusIndicator.classList.remove("offline");
        statusText.textContent = "Online";
      } else {
        statusIndicator.classList.add("offline");
        statusIndicator.classList.remove("online");
        statusText.textContent = "Degraded";
      }
    } catch (error) {
      statusIndicator.classList.add("offline");
      statusIndicator.classList.remove("online");
      statusText.textContent = "Offline";
    }
  }

  // ============================================================================
  // Event Listeners
  // ============================================================================

  // Send message
  chatSend.addEventListener("click", sendMessage);

  chatInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Open/Close chat
  chatButton.addEventListener("click", () => {
    chatSidebar.classList.add("open");
    chatInput.focus();
  });

  chatClose.addEventListener("click", () => {
    chatSidebar.classList.remove("open");
  });

  // Clear chat
  if (clearChatBtn) {
    clearChatBtn.addEventListener("click", clearHistory);
  }

  // Clear cache
  if (clearCacheBtn) {
    clearCacheBtn.addEventListener("click", clearCache);
  }

  // ============================================================================
  // Initialization
  // ============================================================================

  function init() {
    console.log("🤖 EasyDocs Bot initialized");
    console.log(`User: ${username}`);

    // Check bot status
    checkBotStatus();

    // Check status every 30 seconds
    setInterval(checkBotStatus, 30000);

    // Load history
    loadHistory();

    // Show welcome if no history
    if (conversationHistory.length === 0) {
      showWelcome();
    }
  }

  // Start
  init();
});
