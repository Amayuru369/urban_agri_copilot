/* ---------------------------------------------------------------------------
 * UrbanAgri-Copilot — Shared Garden Assistant Chat Widget
 *
 * A single, self-contained component that injects the floating chat launcher
 * (FAB) + slide-up drawer into <body> on load. Loaded by both
 * frontend/index.html and frontend/garden.html (after /auth.js) so the
 * assistant is globally available across the app.
 *
 * Features
 *  • Dynamic DOM injection (no hardcoded markup in the host pages).
 *  • Per-user message persistence  → localStorage["urbanagri_chat_history_<id>"]
 *  • Drawer open/close persistence → sessionStorage["urbanagri_chat_open"]
 *    (stays open as the user navigates between pages in the same tab).
 *  • Markdown rendering of assistant replies via marked.parse().
 *  • Animated typing indicator, disabled Send while in flight, auto-scroll.
 *  • Quick-action suggestion chips above the input.
 *  • "Clear chat" control in the drawer header.
 *  • Authenticated POST /api/chat using getAuthToken() from auth.js, with
 *    graceful 401 handling.
 *
 * Styling relies on Tailwind utility classes (loaded by the host page). A tiny
 * scoped <style> block is injected only to give marked-generated markdown
 * proper typography (Tailwind Preflight strips list/margin defaults) and to
 * hide the chip row scrollbar.
 *
 * Additive: attaches nothing but DOM; never mutates host-page logic.
 * ------------------------------------------------------------------------ */
(function (global) {
  'use strict';

  // Guard against double-injection (e.g. script included twice).
  if (global.__urbanAgriChatWidgetLoaded) return;
  global.__urbanAgriChatWidgetLoaded = true;

  var OPEN_SESSION_KEY = 'urbanagri_chat_open';
  var GREETING =
    "Hello! I'm watching your garden. Ask me anything about your active crops, " +
    'upcoming milestones, or organic remedies. 🌱';

  var QUICK_ACTIONS = [
    { label: '💧 What needs watering?', message: 'What needs watering?' },
    { label: '🔔 Summarize my alerts', message: 'Summarize my alerts' },
    { label: '🐛 Organic pest remedies', message: 'What organic pest remedies do you recommend?' },
  ];

  // ---------------------------------------------------------------------
  // Auth / user helpers (delegate to shared auth.js when available)
  // ---------------------------------------------------------------------
  function getToken() {
    try {
      return typeof global.getAuthToken === 'function' ? global.getAuthToken() : null;
    } catch (_) {
      return null;
    }
  }

  function resolveUserId() {
    // Prefer the page-resolved profile, then fall back to decoding the JWT.
    try {
      if (global.CURRENT_USER && global.CURRENT_USER.id != null) {
        return String(global.CURRENT_USER.id);
      }
    } catch (_) { /* ignore */ }
    try {
      var token = getToken();
      if (token && typeof global.decodeTokenPayload === 'function') {
        var claims = global.decodeTokenPayload(token);
        if (claims && claims.sub != null) return String(claims.sub);
      }
    } catch (_) { /* ignore */ }
    return 'guest';
  }

  var USER_ID = resolveUserId();

  function storageKey() {
    return 'urbanagri_chat_history_' + USER_ID;
  }

  // ---------------------------------------------------------------------
  // Small HTML utilities
  // ---------------------------------------------------------------------
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Light sanitisation for LLM-generated markdown: strip <script>, on* handlers
  // and javascript: URLs. marked does not sanitise by default.
  function sanitizeHtml(html) {
    return String(html)
      .replace(/<\s*script[\s\S]*?<\s*\/\s*script\s*>/gi, '')
      .replace(/<\s*iframe[\s\S]*?<\s*\/\s*iframe\s*>/gi, '')
      .replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
      .replace(/href\s*=\s*(["']?)\s*javascript:[^"'>]*\1/gi, 'href="#"');
  }

  function renderMarkdown(text) {
    if (typeof global.marked !== 'undefined' && global.marked &&
        typeof global.marked.parse === 'function') {
      try {
        if (typeof global.marked.setOptions === 'function') {
          global.marked.setOptions({ breaks: true, gfm: true });
        }
        return sanitizeHtml(global.marked.parse(String(text)));
      } catch (_) { /* fall through to plain rendering */ }
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
  }

  // ---------------------------------------------------------------------
  // Markup injection (Tailwind utility classes only)
  // ---------------------------------------------------------------------
  function buildMarkup() {
    var chips = QUICK_ACTIONS.map(function (a) {
      return (
        '<button type="button" class="uac-chip whitespace-nowrap text-xs font-medium ' +
        'text-emerald-700 bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 ' +
        'rounded-full px-3 py-1.5 transition" data-msg="' + escapeHtml(a.message) + '">' +
        escapeHtml(a.label) + '</button>'
      );
    }).join('');

    return (
      '<style id="chat-widget-style">' +
        '#chat-chips::-webkit-scrollbar{display:none;}' +
        '#chat-chips{-ms-overflow-style:none;scrollbar-width:none;}' +
        '.uac-md{font-size:.875rem;line-height:1.5;word-wrap:break-word;}' +
        '.uac-md p{margin:0 0 .5rem;}.uac-md p:last-child{margin-bottom:0;}' +
        '.uac-md ul{list-style:disc;margin:.25rem 0 .5rem 1.15rem;}' +
        '.uac-md ol{list-style:decimal;margin:.25rem 0 .5rem 1.15rem;}' +
        '.uac-md li{margin:.15rem 0;}' +
        '.uac-md strong{font-weight:700;}.uac-md em{font-style:italic;}' +
        '.uac-md a{color:#059669;text-decoration:underline;}' +
        '.uac-md code{background:#f3f4f6;padding:.1rem .3rem;border-radius:.25rem;font-size:.8rem;}' +
        '.uac-md h1,.uac-md h2,.uac-md h3,.uac-md h4{font-weight:700;margin:.45rem 0 .25rem;}' +
        '.uac-md blockquote{border-left:3px solid #d1d5db;padding-left:.6rem;color:#4b5563;margin:.3rem 0;}' +
      '</style>' +

      '<button id="chat-fab" type="button" title="Ask Garden Copilot" ' +
        'aria-label="Open garden assistant chat" ' +
        'class="fixed bottom-5 right-5 z-[9999] h-14 w-14 rounded-full bg-emerald-600 ' +
        'hover:bg-emerald-700 text-white text-2xl shadow-lg flex items-center justify-center ' +
        'transition-transform duration-200 hover:scale-105 focus:outline-none focus:ring-4 ' +
        'focus:ring-emerald-300">🌿</button>' +

      '<div id="chat-drawer" role="dialog" aria-label="Garden assistant chat" ' +
        'class="fixed bottom-24 right-5 z-[9999] w-[92vw] max-w-sm h-[70vh] max-h-[560px] ' +
        'bg-white rounded-2xl shadow-2xl border border-gray-200 flex flex-col overflow-hidden ' +
        'transition-all duration-200 origin-bottom-right opacity-0 pointer-events-none ' +
        'translate-y-4 scale-95">' +

        // Header
        '<div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 ' +
          'bg-gradient-to-r from-emerald-50 to-green-50">' +
          '<div class="flex items-center gap-2">' +
            '<span class="relative flex h-2.5 w-2.5">' +
              '<span class="animate-ping absolute inline-flex h-full w-full rounded-full ' +
                'bg-emerald-400 opacity-75"></span>' +
              '<span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>' +
            '</span>' +
            '<h3 class="text-sm font-bold text-gray-800">UrbanAgri Copilot</h3>' +
          '</div>' +
          '<div class="flex items-center gap-1">' +
            '<button id="chat-clear" type="button" title="Clear chat history" ' +
              'aria-label="Clear chat history" class="text-gray-400 hover:text-red-500 p-1 ' +
              'rounded transition">' +
              '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
                'd="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6' +
                'm1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>' +
            '</button>' +
            '<button id="chat-close" type="button" title="Minimize" aria-label="Minimize chat" ' +
              'class="text-gray-400 hover:text-gray-600 text-lg leading-none font-bold px-1">✕</button>' +
          '</div>' +
        '</div>' +

        // Messages
        '<div id="chat-messages" class="flex-1 overflow-y-auto px-4 py-3 space-y-3 bg-gray-50"></div>' +

        // Quick-action chips
        '<div id="chat-chips" class="px-3 pt-2 pb-1 flex gap-2 overflow-x-auto border-t ' +
          'border-gray-100 bg-white">' + chips + '</div>' +

        // Input
        '<div class="px-3 py-3 border-t border-gray-200 bg-white">' +
          '<form id="chat-form" class="flex items-center gap-2">' +
            '<input id="chat-input" type="text" placeholder="Ask about your garden..." ' +
              'autocomplete="off" maxlength="500" class="flex-1 text-sm border border-gray-300 ' +
              'rounded-full px-4 py-2 focus:ring-2 focus:ring-emerald-500 ' +
              'focus:border-emerald-500 outline-none" />' +
            '<button type="submit" id="chat-send" class="bg-emerald-600 hover:bg-emerald-700 ' +
              'text-white text-sm font-semibold px-4 py-2 rounded-full transition ' +
              'disabled:opacity-50 disabled:cursor-not-allowed">Send</button>' +
          '</form>' +
        '</div>' +
      '</div>'
    );
  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------
  function boot() {
    if (!global.document || !global.document.body) return;
    if (global.document.getElementById('chat-drawer')) return; // already present

    global.document.body.insertAdjacentHTML('beforeend', buildMarkup());

    var fab = global.document.getElementById('chat-fab');
    var drawer = global.document.getElementById('chat-drawer');
    var closeBtn = global.document.getElementById('chat-close');
    var clearBtn = global.document.getElementById('chat-clear');
    var form = global.document.getElementById('chat-form');
    var input = global.document.getElementById('chat-input');
    var sendBtn = global.document.getElementById('chat-send');
    var messagesEl = global.document.getElementById('chat-messages');
    var chipsEl = global.document.getElementById('chat-chips');

    var conversation = [];   // [{role, content}] persisted per-user
    var isWaiting = false;
    var isOpen = false;

    // ---------------- persistence ----------------
    function loadConversation() {
      try {
        var raw = global.localStorage.getItem(storageKey());
        if (!raw) return [];
        var parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
      } catch (_) {
        return [];
      }
    }

    function saveConversation() {
      try {
        global.localStorage.setItem(storageKey(), JSON.stringify(conversation));
      } catch (_) { /* storage full / disabled — non-fatal */ }
    }

    // ---------------- rendering ----------------
    function scrollToBottom() {
      if (!messagesEl) return;
      global.requestAnimationFrame(function () {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      });
    }

    function appendMessage(role, text) {
      var row = global.document.createElement('div');
      if (role === 'user') {
        row.className = 'flex justify-end';
        var ub = global.document.createElement('div');
        ub.className = 'max-w-[80%] bg-emerald-600 text-white text-sm rounded-2xl ' +
          'rounded-br-sm px-4 py-2 shadow-sm whitespace-pre-wrap break-words';
        ub.textContent = text;
        row.appendChild(ub);
      } else {
        row.className = 'flex justify-start';
        var ab = global.document.createElement('div');
        ab.className = 'uac-md max-w-[80%] bg-white border border-gray-200 text-gray-800 ' +
          'text-sm rounded-2xl rounded-bl-sm px-4 py-2 shadow-sm break-words';
        ab.innerHTML = renderMarkdown(text);
        row.appendChild(ab);
      }
      messagesEl.appendChild(row);
      scrollToBottom();
    }

    function renderGreeting() {
      appendMessage('assistant', GREETING);
    }

    function showTyping() {
      hideTyping();
      var wrap = global.document.createElement('div');
      wrap.id = 'chat-typing-bubble';
      wrap.className = 'flex justify-start';
      wrap.innerHTML =
        '<div class="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 ' +
        'flex items-center gap-1 shadow-sm">' +
          '<span class="h-2 w-2 rounded-full bg-emerald-400 animate-bounce" style="animation-delay:0ms"></span>' +
          '<span class="h-2 w-2 rounded-full bg-emerald-400 animate-bounce" style="animation-delay:150ms"></span>' +
          '<span class="h-2 w-2 rounded-full bg-emerald-400 animate-bounce" style="animation-delay:300ms"></span>' +
        '</div>';
      messagesEl.appendChild(wrap);
      scrollToBottom();
    }

    function hideTyping() {
      var t = global.document.getElementById('chat-typing-bubble');
      if (t && t.parentNode) t.parentNode.removeChild(t);
    }

    // ---------------- drawer open/close ----------------
    function setOpen(open) {
      isOpen = open;
      if (open) {
        drawer.classList.remove('opacity-0', 'pointer-events-none', 'translate-y-4', 'scale-95');
        drawer.classList.add('opacity-100', 'pointer-events-auto', 'translate-y-0', 'scale-100');
        fab.textContent = '✕';
        try { global.sessionStorage.setItem(OPEN_SESSION_KEY, '1'); } catch (_) {}
        global.setTimeout(function () { if (input) input.focus(); }, 180);
      } else {
        drawer.classList.add('opacity-0', 'pointer-events-none', 'translate-y-4', 'scale-95');
        drawer.classList.remove('opacity-100', 'pointer-events-auto', 'translate-y-0', 'scale-100');
        fab.textContent = '🌿';
        try { global.sessionStorage.setItem(OPEN_SESSION_KEY, '0'); } catch (_) {}
      }
    }

    fab.addEventListener('click', function () { setOpen(!isOpen); });
    closeBtn.addEventListener('click', function () { setOpen(false); });

    // ---------------- clear history ----------------
    clearBtn.addEventListener('click', function () {
      if (!global.confirm('Clear this chat history?')) return;
      conversation = [];
      try { global.localStorage.removeItem(storageKey()); } catch (_) {}
      messagesEl.innerHTML = '';
      renderGreeting();
    });

    // ---------------- send ----------------
    async function sendMessage(rawText) {
      var text = (rawText != null ? String(rawText) : input.value).trim();
      if (!text || isWaiting) return;

      appendMessage('user', text);
      conversation.push({ role: 'user', content: text });
      saveConversation();
      input.value = '';

      isWaiting = true;
      sendBtn.disabled = true;
      input.disabled = true;
      showTyping();

      try {
        var headers = { 'Content-Type': 'application/json' };
        var token = getToken();
        if (token) headers['Authorization'] = 'Bearer ' + token;

        var res = await fetch('/api/chat', {
          method: 'POST',
          headers: headers,
          body: JSON.stringify({
            message: text,
            // Prior turns only (backend appends the current message). Cap the
            // window so long-lived persisted threads don't bloat the request.
            history: conversation.slice(0, -1).slice(-20),
          }),
        });

        if (res.status === 401) {
          hideTyping();
          appendMessage(
            'assistant',
            '⚠️ Your session has expired or you are not signed in. Please sign in again to continue chatting.'
          );
          return;
        }

        if (!res.ok) {
          var err = await res.json().catch(function () { return {}; });
          throw new Error(err.detail || ('HTTP ' + res.status));
        }

        var data = await res.json();
        var reply = data.reply || "I couldn't generate a response. Please try again.";
        hideTyping();
        appendMessage('assistant', reply);
        conversation.push({ role: 'assistant', content: reply });
        saveConversation();
      } catch (e) {
        hideTyping();
        appendMessage('assistant', '⚠️ Sorry, something went wrong: ' + e.message + '. Please try again.');
      } finally {
        isWaiting = false;
        sendBtn.disabled = false;
        input.disabled = false;
        if (input) input.focus();
      }
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      sendMessage();
    });

    // ---------------- quick-action chips ----------------
    if (chipsEl) {
      chipsEl.addEventListener('click', function (e) {
        var chip = e.target.closest ? e.target.closest('.uac-chip') : null;
        if (!chip) return;
        var msg = chip.getAttribute('data-msg') || chip.textContent.trim();
        if (isWaiting) return;
        sendMessage(msg);
      });
    }

    // ---------------- initial render ----------------
    conversation = loadConversation();
    if (conversation.length) {
      conversation.forEach(function (m) { appendMessage(m.role, m.content); });
    } else {
      renderGreeting();
    }

    // Restore drawer open-state across page navigation (same tab).
    var wasOpen = '0';
    try { wasOpen = global.sessionStorage.getItem(OPEN_SESSION_KEY) || '0'; } catch (_) {}
    if (wasOpen === '1') {
      // Open immediately without the intro transition fighting the restore.
      drawer.classList.remove('opacity-0', 'pointer-events-none', 'translate-y-4', 'scale-95');
      drawer.classList.add('opacity-100', 'pointer-events-auto', 'translate-y-0', 'scale-100');
      fab.textContent = '✕';
      isOpen = true;
    }
  }

  if (global.document && (global.document.readyState === 'loading')) {
    global.document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
