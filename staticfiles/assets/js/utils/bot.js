// static/js/bot.js
document.addEventListener("DOMContentLoaded", () => {
  const chatButton = document.getElementById("chatBotButton");
  const chatSidebar = document.getElementById("chatBotSidebar");
  const chatClose = document.getElementById("chatBotClose");
  const chatMessages = document.getElementById("chatBotMessages");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");

  // API endpoints
  const username = (window.currentUser || "").trim();
  const forwarderUrl = "/api/bot/enqueue/";
  const resultPollBase = "/api/bot/result/";

  /** Utils **/
  function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function appendMessage(text, who = "bot", extraClass = "") {
    const msgDiv = document.createElement("div");
    msgDiv.classList.add(`${who}-message`);
    if (extraClass) msgDiv.classList.add(extraClass);
    msgDiv.innerText = typeof text === "string" ? text : JSON.stringify(text, null, 2);
    chatMessages.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv;
  }

  /** Typing indicator **/
  let typingIntervals = {};
  function showTypingIndicator(who) {
    if (!document.getElementById(`${who}-typing`)) {
      const elem = document.createElement("div");
      elem.id = `${who}-typing`;
      elem.className = `${who}-message typing`;
      elem.innerText = who === "bot" ? "Bot is typing..." : "You are typing...";
      chatMessages.appendChild(elem);
      scrollToBottom();

      // Animate dots for bot
      if (who === "bot") {
        let dots = 0;
        typingIntervals[who] = setInterval(() => {
          dots = (dots + 1) % 4;
          elem.innerText = "Bot is typing" + ".".repeat(dots);
          scrollToBottom();
        }, 500);
      }
    }
  }

  function hideTypingIndicator(who) {
    const e = document.getElementById(`${who}-typing`);
    if (e) e.remove();
    if (typingIntervals[who]) {
      clearInterval(typingIntervals[who]);
      delete typingIntervals[who];
    }
  }

  chatInput.addEventListener("input", () => {
    clearTimeout(typingTimeout);
    showTypingIndicator("user");
    typingTimeout = setTimeout(() => hideTypingIndicator("user"), 600);
  });

  /** Retry fetch helper **/
  async function fetchWithRetry(url, options = {}, retries = 3, backoff = 1000) {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const response = await fetch(url, options);
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          throw { status: response.status, data };
        }
        return data;
      } catch (err) {
        console.error(`Attempt ${attempt + 1} failed for ${url}`, err);
        if (attempt < retries) {
          await new Promise(r => setTimeout(r, backoff * Math.pow(2, attempt))); // exponential backoff
        } else {
          throw err;
        }
      }
    }
  }

  /** Polling helper with retries and extended timeout **/
  async function pollForResult(requestId, onResult, timeoutMs = 60000, intervalMs = 1500) {
    const start = Date.now();
    showTypingIndicator("bot");

    while (Date.now() - start < timeoutMs) {
      try {
        const data = await fetchWithRetry(
          resultPollBase + encodeURIComponent(requestId) + "/",
          { method: "GET", headers: { "Accept": "application/json" } },
          2,
          1000
        );

        console.log("Polling result:", data);

        if (data.ok && data.result && data.result.answer) {
          hideTypingIndicator("bot");
          return onResult(data);
        } else if (data.error) {
          hideTypingIndicator("bot");
          return onResult({ error: data.error });
        }
        // still pending: continue
      } catch (err) {
        console.error("Polling error:", err);
        hideTypingIndicator("bot");
        return onResult({ error: "Failed to fetch result from server." });
      }
      await new Promise(res => setTimeout(res, intervalMs));
    }

    hideTypingIndicator("bot");
    onResult({ error: "Took too long to get a reply." });
  }

  /** Send message **/
  async function sendMessage() {
    const userText = chatInput.value.trim();
    if (!userText) return;

    hideTypingIndicator("user");
    appendMessage(userText, "user");
    chatInput.value = "";
    chatInput.focus();

    // Always show bot typing immediately
    showTypingIndicator("bot");

    try {
      const data = await fetchWithRetry(
        forwarderUrl,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: userText, username })
        },
        2,
        1000
      );

      console.log("Enqueue response:", data);

      // Fast path
      if (data.ok && data.result && data.result.answer) {
        hideTypingIndicator("bot");
        appendMessage(data.result.answer, "bot");
        return;
      }

      // Deferred path
      if (data.request_id) {
        appendMessage("Bot is processing your request... ⏳", "bot", "typing-indicator");
        await pollForResult(data.request_id, (result) => {
          if (result.ok && result.result && result.result.answer) {
            appendMessage(result.result.answer, "bot");
          } else if (result.error) {
            appendMessage("Oops — " + result.error, "bot");
          } else {
            appendMessage("No reply available.", "bot");
          }
        }, 120000, 2000); // 2 minutes timeout, 2s polling
        return;
      }

      hideTypingIndicator("bot");
      appendMessage("Oops! Something went wrong.", "bot");

    } catch (err) {
      console.error("Send message error:", err);
      hideTypingIndicator("bot");
      appendMessage("Network error. Please try again.", "bot");
    }
  }

  /** Event bindings **/
  chatSend.addEventListener("click", sendMessage);
  chatInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  chatButton.addEventListener("click", () => {
    chatSidebar.classList.add("open");
    chatInput.focus();
  });

  chatClose.addEventListener("click", () => {
    chatSidebar.classList.remove("open");
  });
});
