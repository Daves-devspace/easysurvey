document.addEventListener("DOMContentLoaded", () => {
  const chatButton = document.getElementById("chatBotButton");
  const chatSidebar = document.getElementById("chatBotSidebar");
  const chatClose = document.getElementById("chatBotClose");
  const chatMessages = document.getElementById("chatBotMessages");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");

  let knowledgeBase = [];

  // 🔹 If Django template provides username (or null if anonymous)
  const username = (window.currentUser || "").trim();

  //const username = "{{ request.user.username|default:'' }}".trim();

  // Toggle sidebar
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

  // Load knowledge base
  async function loadKnowledgeBase() {
    try {
      const res = await fetch("/static/assets/json/knowledgeBase.json");
      knowledgeBase = await res.json();
      console.log("Knowledge base loaded:", knowledgeBase.length, "entries");
    } catch (err) {
      console.error("Failed to load knowledge base:", err);
    }
  }
  loadKnowledgeBase();

  // Find best answer using HF API
// helper: detect greeting-like answers and return personalized version
function personalizeGreeting(answer, username) {
  if (!answer || typeof answer !== "string") return answer;

  // Matches greetings at the start and captures the rest of the answer
  // Groups: 1 = full greeting prefix, 2 = remainder after greeting
  const greetingRegex = /^\s*(?:hi|hello|hey|greetings|good (?:morning|afternoon|evening))[\s,!.:-]*(.*)$/i;
  const m = answer.match(greetingRegex);

  if (!m) {
    // Not a greeting phrase at the start
    return answer;
  }

  const remainder = (m[1] || "").trim();

  if (username) {
    // If KB answer is only greeting (no remainder), supply a friendly default tail
    if (!remainder) {
      return `Hi ${username}! How can I help you today?`;
    }
    // Otherwise replace generic start with personalized greeting + remainder
    return `Hi ${username}, ${remainder}`;
  } else {
    // No username available — return KB answer as-is
    return answer;
  }
}

async function findBestAnswer(userText) {
  if (!knowledgeBase.length) return "Knowledge base not loaded yet.";

  try {
    const res = await fetch("/api/get-similarity/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_sentence: userText }),
    });

    if (!res.ok) {
      console.error("Similarity API error", res.status);
      const errData = await res.json().catch(() => null);
      console.error(errData);
      return "I'm sorry, I couldn't process that request right now.";
    }

    const data = await res.json();
    const answer = data.answer;
    const score = data.score;

    // If no strong match, polite fallback (no username)
    if (!answer || score == null || score < 0.3) {
      return "I didn’t find a strong match. You might try asking about Clients, Services, Bookings, Documents, Accounts, or Employees.";
    }

    // If answer looks like a greeting, personalize it, otherwise return answer unchanged
    return personalizeGreeting(answer, username);

  } catch (err) {
    console.error(err);
    return "Sorry, something went wrong. Please try again.";
  }
}

  // Send message
  async function sendMessage() {
    const userText = chatInput.value.trim();
    if (!userText) return;

    const userMsgDiv = document.createElement("div");
    userMsgDiv.classList.add("user-message");
    userMsgDiv.innerText = userText;
    chatMessages.appendChild(userMsgDiv);
    scrollToBottom();

    const botReply = await findBestAnswer(userText);
    const botMsgDiv = document.createElement("div");
    botMsgDiv.classList.add("bot-message");
    botMsgDiv.innerText = botReply;
    chatMessages.appendChild(botMsgDiv);
    scrollToBottom();

    chatInput.value = "";
    chatInput.focus();
  }

  chatSend.addEventListener("click", sendMessage);
  chatInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") sendMessage();
  });
});
