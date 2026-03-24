(function () {
  "use strict";

  function detectReportType() {
    const title = document.title.toLowerCase();
    if (title.includes("events")) return "events";
    if (title.includes("email")) return "email";
    return "weekly";
  }

  const THEME = {
    weekly: { color: "#b8654a", label: "Weekly Digest AI" },
    events: { color: "#7092fb", label: "Events Report AI" },
    email:  { color: "#2d7060", label: "Email Report AI" },
  };

  const SUGGESTIONS = {
    weekly: [
      "What's the biggest concern this week?",
      "How is Qualified pipeline pacing?",
      "Which region is furthest behind?",
      "What were the top wins?",
    ],
    events: [
      "Which event generated the most pipeline?",
      "How is events Discovery pacing vs. OKR?",
      "What's the top recommended action?",
      "How did events perform WoW?",
    ],
    email: [
      "What's the open rate vs. benchmark?",
      "Which email program had the best CTR?",
      "How did the March newsletter perform?",
      "What are the recommended actions?",
    ],
  };

  const reportType = detectReportType();
  const theme = THEME[reportType];
  const ACCENT = theme.color;

  const style = document.createElement("style");
  style.textContent = `
    #ask-ai-btn {
      position: fixed; bottom: 28px; right: 28px; z-index: 9000;
      background: ${ACCENT}; color: #fff; border: none; border-radius: 100px;
      padding: 13px 20px; font-family: 'Inter', sans-serif; font-size: 13.5px;
      font-weight: 600; letter-spacing: -0.2px; cursor: pointer;
      box-shadow: 0 4px 20px rgba(0,0,0,0.18);
      display: flex; align-items: center; gap: 7px;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    #ask-ai-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(0,0,0,0.22); }

    #ask-ai-overlay {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.3); z-index: 9100; backdrop-filter: blur(2px);
    }
    #ask-ai-overlay.open { display: block; }

    #ask-ai-drawer {
      position: fixed; bottom: 0; right: 0; width: 420px; max-width: 100vw;
      height: 520px; max-height: 85vh; background: #fff;
      border-radius: 16px 16px 0 0; z-index: 9200;
      display: flex; flex-direction: column;
      box-shadow: 0 -8px 40px rgba(0,0,0,0.14);
      transform: translateY(100%);
      transition: transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94);
    }
    #ask-ai-drawer.open { transform: translateY(0); }

    .ask-ai-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 18px 20px 14px; border-bottom: 1px solid #f0f0ee; flex-shrink: 0;
    }
    .ask-ai-title { font-family: 'Inter', sans-serif; font-size: 14.5px; font-weight: 600; color: #1a1a1a; letter-spacing: -0.3px; }
    .ask-ai-subtitle { font-family: 'Inter', sans-serif; font-size: 11.5px; color: #aaa; margin-top: 1px; }
    .ask-ai-close {
      background: #f5f4f0; border: none; border-radius: 50%;
      width: 30px; height: 30px; cursor: pointer; font-size: 14px; color: #666;
      display: flex; align-items: center; justify-content: center;
      transition: background 0.15s ease;
    }
    .ask-ai-close:hover { background: #e8e8e5; }

    .ask-ai-messages {
      flex: 1; overflow-y: auto; padding: 14px 18px;
      display: flex; flex-direction: column; gap: 10px;
    }
    .msg {
      max-width: 88%; padding: 9px 13px; border-radius: 12px;
      font-family: 'Inter', sans-serif; font-size: 13px; line-height: 1.55;
    }
    .msg-user { align-self: flex-end; background: ${ACCENT}; color: #fff; border-bottom-right-radius: 4px; }
    .msg-ai { align-self: flex-start; background: #f5f4f0; color: #1a1a1a; border-bottom-left-radius: 4px; }
    .msg-ai.loading { color: #aaa; font-style: italic; }
    .msg-error { align-self: flex-start; background: #fff0ed; color: #c0392b; border-bottom-left-radius: 4px; font-size: 12.5px; }

    .ask-ai-suggestions {
      padding: 0 18px 8px; display: flex; flex-wrap: wrap; gap: 6px; flex-shrink: 0;
    }
    .ask-ai-suggestions.hidden { display: none; }
    .suggestion-chip {
      background: #f5f4f0; border: 1px solid #e8e8e5; border-radius: 100px;
      padding: 5px 11px; font-family: 'Inter', sans-serif; font-size: 11.5px;
      color: #555; cursor: pointer; transition: border-color 0.15s ease, color 0.15s ease;
    }
    .suggestion-chip:hover { border-color: ${ACCENT}; color: ${ACCENT}; }

    .ask-ai-input-row {
      display: flex; align-items: flex-end; gap: 8px;
      padding: 10px 18px 18px; border-top: 1px solid #f0f0ee; flex-shrink: 0;
    }
    .ask-ai-input {
      flex: 1; border: 1.5px solid #e8e8e5; border-radius: 12px;
      padding: 9px 13px; font-family: 'Inter', sans-serif; font-size: 13px;
      color: #1a1a1a; resize: none; outline: none; line-height: 1.5;
      transition: border-color 0.15s ease; min-height: 40px; max-height: 110px;
    }
    .ask-ai-input:focus { border-color: ${ACCENT}; }
    .ask-ai-input::placeholder { color: #bbb; }
    .ask-ai-send {
      background: ${ACCENT}; color: #fff; border: none; border-radius: 10px;
      width: 38px; height: 38px; cursor: pointer; font-size: 16px;
      display: flex; align-items: center; justify-content: center; flex-shrink: 0;
      transition: opacity 0.15s ease;
    }
    .ask-ai-send:disabled { opacity: 0.4; cursor: not-allowed; }
    .ask-ai-send:not(:disabled):hover { opacity: 0.85; }

    @media (max-width: 480px) {
      #ask-ai-drawer { width: 100vw; }
      #ask-ai-btn { bottom: 20px; right: 16px; }
    }
  `;
  document.head.appendChild(style);

  // Floating button
  const btn = document.createElement("button");
  btn.id = "ask-ai-btn";
  btn.innerHTML = `<span style="font-size:15px;line-height:1">✦</span> Ask AI`;
  document.body.appendChild(btn);

  // Overlay
  const overlay = document.createElement("div");
  overlay.id = "ask-ai-overlay";
  document.body.appendChild(overlay);

  // Drawer
  const drawer = document.createElement("div");
  drawer.id = "ask-ai-drawer";
  drawer.innerHTML = `
    <div class="ask-ai-header">
      <div>
        <div class="ask-ai-title">${theme.label}</div>
        <div class="ask-ai-subtitle">Ask anything about this report</div>
      </div>
      <button class="ask-ai-close" aria-label="Close">✕</button>
    </div>
    <div class="ask-ai-messages" id="ask-ai-messages"></div>
    <div class="ask-ai-suggestions" id="ask-ai-suggestions">
      ${SUGGESTIONS[reportType].map(q => `<button class="suggestion-chip">${q}</button>`).join("")}
    </div>
    <div class="ask-ai-input-row">
      <textarea class="ask-ai-input" id="ask-ai-input"
        placeholder="e.g. How much pipeline did Events generate?" rows="1"></textarea>
      <button class="ask-ai-send" id="ask-ai-send" aria-label="Send" disabled>↑</button>
    </div>
  `;
  document.body.appendChild(drawer);

  // State
  let isLoading = false;
  let cachedPageText = null;

  function getPageText() {
    if (cachedPageText) return cachedPageText;
    const clone = document.body.cloneNode(true);
    ["ask-ai-btn", "ask-ai-overlay", "ask-ai-drawer"].forEach(id => {
      const el = clone.querySelector("#" + id);
      if (el) el.remove();
    });
    clone.querySelectorAll("style, script, noscript").forEach(el => el.remove());
    cachedPageText = (clone.innerText || clone.textContent || "").replace(/\s{3,}/g, "\n\n").trim();
    return cachedPageText;
  }

  const messagesEl = document.getElementById("ask-ai-messages");
  const inputEl    = document.getElementById("ask-ai-input");
  const sendBtn    = document.getElementById("ask-ai-send");
  const suggestEl  = document.getElementById("ask-ai-suggestions");

  function addMessage(text, type) {
    const div = document.createElement("div");
    div.className = "msg msg-" + type;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function openDrawer() {
    overlay.classList.add("open");
    drawer.classList.add("open");
    btn.style.display = "none";
    setTimeout(() => inputEl.focus(), 300);
  }

  function closeDrawer() {
    overlay.classList.remove("open");
    drawer.classList.remove("open");
    btn.style.display = "";
  }

  btn.addEventListener("click", openDrawer);
  overlay.addEventListener("click", closeDrawer);
  drawer.querySelector(".ask-ai-close").addEventListener("click", closeDrawer);

  inputEl.addEventListener("input", function () {
    sendBtn.disabled = !this.value.trim() || isLoading;
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 110) + "px";
  });

  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!sendBtn.disabled) sendQuestion(); }
  });

  sendBtn.addEventListener("click", sendQuestion);

  suggestEl.querySelectorAll(".suggestion-chip").forEach(chip => {
    chip.addEventListener("click", function () {
      inputEl.value = this.textContent;
      sendBtn.disabled = false;
      suggestEl.classList.add("hidden");
      sendQuestion();
    });
  });

  async function sendQuestion() {
    const question = inputEl.value.trim();
    if (!question || isLoading) return;

    suggestEl.classList.add("hidden");
    addMessage(question, "user");
    inputEl.value = "";
    inputEl.style.height = "auto";
    sendBtn.disabled = true;
    isLoading = true;

    const loadingMsg = addMessage("Thinking…", "ai loading");

    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, pageText: getPageText(), reportType }),
      });
      const data = await response.json();
      loadingMsg.remove();
      if (!response.ok || data.error) {
        addMessage(data.error || "Something went wrong. Please try again.", "error");
      } else {
        addMessage(data.answer, "ai");
      }
    } catch {
      loadingMsg.remove();
      addMessage("Network error — please check your connection and try again.", "error");
    } finally {
      isLoading = false;
      sendBtn.disabled = !inputEl.value.trim();
    }
  }
})();
