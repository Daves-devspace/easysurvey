document.addEventListener("DOMContentLoaded", () => {
  const chatButton = document.getElementById("chatBotButton");
  const chatSidebar = document.getElementById("chatBotSidebar");
  const chatClose = document.getElementById("chatBotClose");
  const chatMessages = document.getElementById("chatBotMessages");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");

  const username = (window.currentUser || "").trim();

  // Sidebar toggle
  chatButton.addEventListener("click", () => {
    chatSidebar.style.right = "0";
    chatInput.focus();
  });
  chatClose.addEventListener("click", () => {
    chatSidebar.style.right = "-400px";
  });

  function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // ---------------------
  // Typing indicator
  let typingTimeout;
  chatInput.addEventListener("input", () => {
    clearTimeout(typingTimeout);
    showTypingIndicator("user");
    typingTimeout = setTimeout(() => hideTypingIndicator("user"), 500); // hides after 0.5s of inactivity
  });

  function showTypingIndicator(who) {
    let elem = document.getElementById(`${who}-typing`);
    if (!elem) {
      elem = document.createElement("div");
      elem.id = `${who}-typing`;
      elem.className = `${who}-message typing`;
      elem.innerText = who === "bot" ? "Bot is typing..." : "You are typing...";
      chatMessages.appendChild(elem);
      scrollToBottom();
    }
  }

  function hideTypingIndicator(who) {
    const elem = document.getElementById(`${who}-typing`);
    if (elem) elem.remove();
  }

  // ---------------------
  async function findBestAnswer(userText) {
    showTypingIndicator("bot"); // show bot typing
    try {
      const res = await fetch("/api/get-similarity/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: userText, username }),
      });

      if (!res.ok) return "I'm sorry, I couldn’t process that request right now.";

      const data = await res.json();
      return data.answer || "I’m not sure how to help with that yet.";
    } catch (err) {
      console.error(err);
      return "Sorry, something went wrong. Please try again.";
    } finally {
      hideTypingIndicator("bot"); // hide after response
    }
  }

  // ---------------------
  async function sendMessage() {
    const userText = chatInput.value.trim();
    if (!userText) return;

    // Remove previous user typing indicator
    hideTypingIndicator("user");

    // Add user message
    const userMsgDiv = document.createElement("div");
    userMsgDiv.classList.add("user-message");
    userMsgDiv.innerText = userText;
    chatMessages.appendChild(userMsgDiv);
    scrollToBottom();

    chatInput.value = "";
    chatInput.focus();

    // Get bot reply
    const botReply = await findBestAnswer(userText);
    const botMsgDiv = document.createElement("div");
    botMsgDiv.classList.add("bot-message");
    botMsgDiv.innerText = botReply;
    chatMessages.appendChild(botMsgDiv);
    scrollToBottom();
  }

  chatSend.addEventListener("click", sendMessage);
  chatInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") sendMessage();
  });
});
