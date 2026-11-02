/* TG Kit app: chat UI over the local API. Every piece of chat data is put on the
   page with textContent or text nodes, never parsed as HTML. */
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="tgkit-token"]').content;
  const params = new URLSearchParams(location.search);
  const LANG = (params.get("lang") || navigator.language || "en").toLowerCase().startsWith("vi") ? "vi" : "en";
  document.documentElement.lang = LANG;
  if (params.get("theme") === "dark" || params.get("theme") === "light") {
    document.documentElement.dataset.theme = params.get("theme");
  }

  const plural = (n, one, many) => (n === 1 ? one : many);
  const STR = {
    en: {
      newChat: "New conversation", filter: "Filter conversations and chats", conversations: "Conversations",
      chats: "Your chats", noConvs: "Your questions will be kept here.", noChats: "No chats yet.",
      noMatch: "Nothing matches the filter.",
      sync: "Get new messages", syncing: "Getting new messages...",
      syncDone: (n) => (n ? `${n} new ${plural(n, "message", "messages")}.` : "Everything is up to date."),
      syncFail: "Some chats could not be updated.", syncBusy: "A sync is already running.",
      connect: "Connect to Telegram", answersBy: "Answers", auto: "Claude when available", offline: "TG Kit only",
      modeClaude: "Claude", modeOffline: "Offline",
      claudeOn: "Claude Code is on this computer: it plans each search and writes the answer.",
      claudeOff: "Claude Code is not on this computer: TG Kit answers with its own rules.",
      offlineOn: "TG Kit answers with its own rules, without Claude.",
      emptyTitle: "Ask about your Telegram chats",
      emptyBody: "Search messages, get a summary of what happened, or see your tasks. Type in Vietnamese or English, accents optional.",
      inputPh: "Ask about your chats", hint: "Enter to send · Shift+Enter for a new line · Esc to stop",
      send: "Send", stop: "Stop", thinking: "Looking through your chats...", stopped: "Stopped.",
      steps: (n) => `Ran ${n} TG Kit ${plural(n, "command", "commands")}`, stepOk: "done", stepBad: "failed",
      showCtx: "Show conversation", hideCtx: "Hide conversation",
      messagesN: (n) => `${n} ${plural(n, "message", "messages")}`,
      tasksN: (n) => `${n} ${plural(n, "task", "tasks")}`,
      chatsN: (n) => `${n} ${plural(n, "chat", "chats")}`,
      markDone: "Mark done", undo: "Undo", forYou: "For you", due: "Due",
      stale: "May be out of date", staleNote: (names) => `Last updated more than 12 hours ago: ${names}. Press Get new messages for a fresh copy.`,
      people: "People", threads: "Busiest conversations", questions: "Questions", decisions: "Possible decisions",
      decisionsNote: "Check what each one replies to before relying on it.", requests: "Requests and to-dos",
      links: "Links", files: "Files", replyingTo: "Replying to", notLocal: "a message not in the local copy",
      quiet: "No messages in this period", remove: "Remove conversation", in: "in",
      notConnected: "TG Kit is not connected to Telegram yet",
      notConnectedBody: "Connect once: a window opens and asks for your Telegram login. Come back here when it is done.",
      setupOpened: "The setup window is open. Come back here when it is done.",
      errGeneric: "Something went wrong. Try again.", errOffline: "TG Kit stopped responding. Close this window and open TG Kit again.",
      wordsMatch: "Every word, in any order", messagesFor: (q) => `“${q}”`, menu: "Show conversations",
      updated: "updated", never: "never", you: "You", summaryOf: (l) => `Summary: ${l}`, yourTasks: "Tasks",
      allChats: "Your chats", file: "File", examplesHead: "Try asking",
    },
    vi: {
      newChat: "Cuộc trò chuyện mới", filter: "Lọc cuộc trò chuyện và nhóm", conversations: "Cuộc trò chuyện",
      chats: "Nhóm của bạn", noConvs: "Câu hỏi của bạn sẽ được lưu ở đây.", noChats: "Chưa có nhóm nào.",
      noMatch: "Không có mục nào khớp.",
      sync: "Lấy tin mới", syncing: "Đang lấy tin mới...",
      syncDone: (n) => (n ? `Có ${n} tin nhắn mới.` : "Đã cập nhật đầy đủ."),
      syncFail: "Một số nhóm chưa cập nhật được.", syncBusy: "Đang có một lượt lấy tin khác.",
      connect: "Kết nối Telegram", answersBy: "Trả lời bằng", auto: "Claude nếu có", offline: "Chỉ TG Kit",
      modeClaude: "Claude", modeOffline: "Ngoại tuyến",
      claudeOn: "Máy này có Claude Code: Claude lên kế hoạch tìm kiếm và viết câu trả lời.",
      claudeOff: "Máy này chưa có Claude Code: TG Kit tự trả lời theo quy tắc có sẵn.",
      offlineOn: "TG Kit tự trả lời theo quy tắc có sẵn, không dùng Claude.",
      emptyTitle: "Hỏi về các nhóm Telegram của bạn",
      emptyBody: "Tìm tin nhắn, tóm tắt những gì đã diễn ra, hoặc xem việc cần làm. Gõ tiếng Việt hay tiếng Anh, có dấu hay không dấu đều được.",
      inputPh: "Hỏi về các nhóm của bạn", hint: "Enter để gửi · Shift+Enter để xuống dòng · Esc để dừng",
      send: "Gửi", stop: "Dừng", thinking: "Đang xem các nhóm của bạn...", stopped: "Đã dừng.",
      steps: (n) => `Đã chạy ${n} lệnh TG Kit`, stepOk: "xong", stepBad: "lỗi",
      showCtx: "Xem cả đoạn", hideCtx: "Ẩn đoạn",
      messagesN: (n) => `${n} tin nhắn`, tasksN: (n) => `${n} việc`, chatsN: (n) => `${n} nhóm`,
      markDone: "Xong", undo: "Hoàn tác", forYou: "Của bạn", due: "Hạn",
      stale: "Có thể đã cũ", staleNote: (names) => `Chưa cập nhật hơn 12 giờ: ${names}. Bấm Lấy tin mới để cập nhật.`,
      people: "Người nhắn", threads: "Trao đổi nhiều nhất", questions: "Câu hỏi", decisions: "Có thể đã chốt",
      decisionsNote: "Xem tin được trả lời trước khi coi là đã đồng ý.", requests: "Yêu cầu và việc cần làm",
      links: "Liên kết", files: "Tệp", replyingTo: "Trả lời", notLocal: "một tin không có trong bản sao",
      quiet: "Không có tin trong khoảng này", remove: "Xóa cuộc trò chuyện", in: "trong",
      notConnected: "TG Kit chưa kết nối với Telegram",
      notConnectedBody: "Kết nối một lần: một cửa sổ sẽ mở ra để bạn đăng nhập Telegram. Xong thì quay lại đây.",
      setupOpened: "Cửa sổ cài đặt đã mở. Xong thì quay lại đây.",
      errGeneric: "Có lỗi xảy ra. Thử lại nhé.", errOffline: "TG Kit không phản hồi. Đóng cửa sổ này và mở lại TG Kit.",
      wordsMatch: "Đủ các từ, không theo thứ tự", messagesFor: (q) => `“${q}”`, menu: "Hiện danh sách",
      updated: "cập nhật", never: "chưa bao giờ", you: "Bạn", summaryOf: (l) => `Tóm tắt: ${l}`, yourTasks: "Việc cần làm",
      allChats: "Nhóm của bạn", file: "Tệp", examplesHead: "Thử hỏi",
    },
  };
  const EXAMPLES = {
    vi: ["Tuần này các nhóm bàn gì?", "Việc của tôi", "tìm biên bản họp", "Hôm nay có gì mới?"],
    en: ["What happened this week?", "My open tasks", "find meeting minutes", "Summarize the last 30 days"],
  };
  const t = (key, ...args) => {
    const v = STR[LANG][key] ?? STR.en[key] ?? key;
    return typeof v === "function" ? v(...args) : v;
  };

  const $ = (id) => document.getElementById(id);
  const ui = {
    shell: $("shell"), side: $("side"), newChat: $("newChat"), filter: $("filter"),
    convList: $("convList"), convEmpty: $("convEmpty"), chatList: $("chatList"), chatEmpty: $("chatEmpty"),
    syncBtn: $("syncBtn"), syncProgress: $("syncProgress"), syncBar: $("syncBar"), syncStatus: $("syncStatus"),
    modeNote: $("modeNote"), menuBtn: $("menuBtn"), title: $("title"), modeBadge: $("modeBadge"),
    scroller: $("scroller"), thread: $("thread"), empty: $("empty"), setupBox: $("setupBox"),
    setupBtn: $("setupBtn"), examples: $("examples"), composer: $("composer"), input: $("input"),
    send: $("send"), sendLabel: $("sendLabel"), version: $("version"),
  };

  const app = {
    conv: null, convs: [], chats: [], claude: false, busy: false, ctrl: null, syncing: false,
    mode: localStorage.getItem("tgkit-mode") === "offline" ? "offline" : "auto",
  };

  // ------------------------------------------------------------ helpers

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  function fold(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
  }

  // Text with every query word marked, matching with or without accents.
  function highlight(text, query) {
    const frag = document.createDocumentFragment();
    text = text || "";
    const words = (query || "").split(/\s+/).map(fold).filter((w) => w.length > 1);
    if (!words.length) { frag.append(text); return frag; }
    let folded = "";
    const map = [];
    for (let i = 0; i < text.length; i++) {
      const f = fold(text[i]);
      for (let k = 0; k < f.length; k++) { folded += f[k]; map.push(i); }
    }
    const marks = [];
    for (const w of words) {
      let from = 0, at;
      while ((at = folded.indexOf(w, from)) >= 0) {
        marks.push([map[at], map[at + w.length - 1] + 1]);
        from = at + w.length;
      }
    }
    marks.sort((a, b) => a[0] - b[0]);
    // One mark over "Biên bản", not two: join ranges split only by spaces.
    const merged = [];
    for (const [a, b] of marks) {
      const last = merged[merged.length - 1];
      if (last && a <= last[1]) last[1] = Math.max(last[1], b);
      else if (last && !text.slice(last[1], a).trim()) last[1] = b;
      else merged.push([a, b]);
    }
    let pos = 0;
    for (const [a, b] of merged) {
      frag.append(text.slice(pos, a), el("mark", {}, text.slice(a, b)));
      pos = b;
    }
    frag.append(text.slice(pos));
    return frag;
  }

  // A small, safe subset of Markdown for Claude's answers: paragraphs, lists, **bold**, `code`, links.
  function inline(text) {
    const frag = document.createDocumentFragment();
    const rx = /(\*\*[^*]+\*\*|`[^`]+`|https?:\/\/[^\s<>"')\]]+)/g;
    let pos = 0, m;
    while ((m = rx.exec(text))) {
      frag.append(text.slice(pos, m.index));
      const tok = m[0];
      if (tok.startsWith("**")) frag.append(el("strong", {}, tok.slice(2, -2)));
      else if (tok.startsWith("`")) frag.append(el("code", {}, tok.slice(1, -1)));
      else frag.append(el("a", { href: tok, target: "_blank", rel: "noopener noreferrer" }, tok));
      pos = m.index + tok.length;
    }
    frag.append(text.slice(pos));
    return frag;
  }

  function markdown(text) {
    const frag = document.createDocumentFragment();
    let list = null, para = [];
    const flush = () => {
      if (para.length) { frag.append(el("p", {}, inline(para.join(" ")))); para = []; }
    };
    for (const raw of (text || "").split("\n")) {
      const line = raw.trimEnd();
      const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (bullet) {
        flush();
        const ordered = /^\s*\d/.test(line);
        if (!list || (list.tagName === "OL") !== ordered) { list = el(ordered ? "ol" : "ul"); frag.append(list); }
        list.append(el("li", {}, inline(bullet[1])));
        continue;
      }
      list = null;
      if (!line.trim()) { flush(); continue; }
      const head = line.match(/^#{1,6}\s+(.*)$/);
      if (head) { flush(); frag.append(el("p", {}, el("strong", {}, inline(head[1])))); continue; }
      para.push(line.trim());
    }
    flush();
    return frag;
  }

  function relTime(iso) {
    if (!iso) return t("never");
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const sec = (d.getTime() - Date.now()) / 1000;
    const rtf = new Intl.RelativeTimeFormat(LANG, { numeric: "auto" });
    const steps = [[60, "second"], [60, "minute"], [24, "hour"], [7, "day"], [4.35, "week"], [12, "month"]];
    let v = sec;
    for (const [n, unit] of steps) {
      if (Math.abs(v) < n) return rtf.format(Math.round(v), unit);
      v /= n;
    }
    return rtf.format(Math.round(v), "year");
  }

  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: { "X-TGKit-Token": TOKEN, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw Object.assign(new Error(t("errOffline")), { data: {} });
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.message || t("errGeneric")), { data });
    return data;
  }

  async function stream(path, body, onEvent, signal) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "X-TGKit-Token": TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal,
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw Object.assign(new Error(data.message || t("errGeneric")), { data });
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (line.trim()) onEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
  }

  // ------------------------------------------------------------ chrome

  function applyStrings() {
    document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
    document.querySelectorAll("[data-i18n-ph]").forEach((n) => { n.placeholder = t(n.dataset.i18nPh); });
    document.querySelectorAll("[data-i18n-label]").forEach((n) => { n.setAttribute("aria-label", t(n.dataset.i18nLabel)); });
    ui.examples.replaceChildren(...EXAMPLES[LANG].map((q) =>
      el("li", {}, el("button", { class: "chip", type: "button", onclick: () => ask(q) }, q))));
  }

  function setMode() {
    const claude = app.claude && app.mode !== "offline";
    ui.modeBadge.textContent = claude ? t("modeClaude") : t("modeOffline");
    ui.modeBadge.classList.toggle("offline", !claude);
    ui.modeNote.textContent = app.mode === "offline" ? t("offlineOn") : (app.claude ? t("claudeOn") : t("claudeOff"));
    document.querySelectorAll('input[name="mode"]').forEach((r) => { r.checked = r.value === app.mode; });
  }

  function setBusy(busy) {
    app.busy = busy;
    ui.send.classList.toggle("stop", busy);
    ui.send.type = busy ? "button" : "submit";
    ui.sendLabel.textContent = busy ? t("stop") : t("send");
  }

  function showEmpty(show) {
    ui.empty.hidden = !show;
  }

  function renderLists() {
    const q = fold(ui.filter.value.trim());
    const convs = app.convs.filter((c) => !q || fold(c.title).includes(q));
    ui.convList.replaceChildren(...convs.map((c) => el("li", {
      class: "item", "aria-current": app.conv && app.conv.id === c.id ? "true" : null,
    },
    el("button", { class: "item-main", type: "button", onclick: () => openConversation(c.id) },
      el("span", { class: "item-title" }, c.title),
      el("span", { class: "item-meta" }, relTime(c.updated))),
    el("button", {
      class: "item-action", type: "button", "aria-label": t("remove"), title: t("remove"),
      onclick: () => removeConversation(c.id),
    }, closeIcon()))));
    ui.convEmpty.hidden = convs.length > 0;
    ui.convEmpty.textContent = app.convs.length ? t("noMatch") : t("noConvs");

    const chats = app.chats.filter((c) => !q || fold(c.title).includes(q));
    ui.chatList.replaceChildren(...chats.map((c) => el("li", { class: "item" },
      el("button", {
        class: "item-main", type: "button",
        onclick: () => ask(LANG === "vi" ? `Tóm tắt ${c.title} tuần này` : `Summarize ${c.title} this week`),
      },
      el("span", { class: "item-title" }, c.title),
      el("span", { class: "item-meta" },
        el("span", { class: c.stale ? "fresh old" : "fresh", title: c.stale ? t("stale") : null },
          `${t("messagesN", c.messages)}, ${t("updated")} ${relTime(c.synced_at)}`))))));
    ui.chatEmpty.hidden = chats.length > 0;
    ui.chatEmpty.textContent = app.chats.length ? t("noMatch") : t("noChats");
  }

  function closeIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("width", "14");
    svg.setAttribute("height", "14");
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", "M4 4l8 8M12 4l-8 8");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", "1.6");
    p.setAttribute("stroke-linecap", "round");
    svg.append(p);
    return svg;
  }

  async function refresh() {
    const s = await api("/api/state");
    app.claude = s.claude;
    app.convs = s.conversations || [];
    app.chats = s.chats || [];
    ui.version.textContent = `v${s.version}`;
    ui.setupBox.hidden = !(!s.logged_in && !app.chats.length);
    setMode();
    renderLists();
    return s;
  }

  // ------------------------------------------------------------ cards

  function metaRow(item, extra) {
    return el("div", { class: "row-meta" },
      item.sender ? el("span", { class: "who" }, item.sender) : null,
      item.chat ? el("span", {}, `${t("in")} ${item.chat}`) : null,
      item.time ? el("time", {}, item.time) : null,
      extra || null);
  }

  function contextToggle(item) {
    const box = el("div", { class: "context", hidden: true });
    const btn = el("button", { class: "link-btn", type: "button" }, t("showCtx"));
    btn.addEventListener("click", async () => {
      if (!box.hidden) { box.hidden = true; btn.textContent = t("showCtx"); return; }
      if (!box.childElementCount) {
        try {
          const data = await api(`/api/thread?folder=${encodeURIComponent(item.folder)}&id=${encodeURIComponent(item.id)}`);
          box.append(...data.items.map((m) => el("div", { class: m.anchor ? "context-line anchor" : "context-line" },
            metaRow({ sender: m.sender, time: m.time }),
            el("p", { class: "ctx-text" }, m.text || (m.file ? `${t("file")}: ${m.file}` : "")))));
        } catch (e) {
          box.append(el("p", {}, e.message));
        }
      }
      box.hidden = false;
      btn.textContent = t("hideCtx");
    });
    return [el("div", { class: "row-actions" }, btn), box];
  }

  function hitsCard(c) {
    const head = el("header", { class: "card-head" },
      el("h2", {}, t("messagesFor", c.query || "")),
      el("span", { class: "count" }, c.match === "words" ? `${t("messagesN", c.total)}, ${t("wordsMatch")}` : t("messagesN", c.total)));
    const note = c.stale && c.stale.length ? el("p", { class: "card-note" }, t("staleNote", c.stale.join(", "))) : null;
    const rows = el("ul", { class: "rows" }, c.items.map((h) => el("li", { class: "row" },
      metaRow(h),
      h.text ? el("p", { class: "snippet" }, highlight(h.text, c.query)) : null,
      h.file ? el("span", { class: "file-chip" }, `${t("file")}: `, highlight(h.file, c.query)) : null,
      h.folder ? contextToggle(h) : null)));
    return el("section", { class: "card" }, head, note, rows);
  }

  function sumList(items, render) {
    return el("ul", { class: "sum-list" }, items.map((it) => el("li", {}, render(it))));
  }

  function quoteLine(it) {
    const r = it.in_reply_to;
    if (!r) return null;
    const who = r.sender ? `${t("replyingTo")} ${r.sender}: ` : `${t("replyingTo")} `;
    return el("p", { class: "quote" }, who, r.text !== null && r.text !== undefined ? `“${r.text}”` : t("notLocal"));
  }

  function msgLine(it) {
    return [metaRow({ sender: it.sender, time: it.time }, it.due ? el("span", { class: "due" }, `${t("due")}: ${it.due}`) : null),
      el("p", { class: "snippet" }, it.text || ""), quoteLine(it)];
  }

  function summaryCard(c) {
    const head = el("header", { class: "card-head" },
      el("h2", {}, t("summaryOf", c.label || "")),
      el("span", { class: "count" }, `${t("messagesN", c.total)}, ${t("chatsN", c.chats.length)}`));
    const parts = c.chats.map((ch, i) => {
      const body = el("div", { class: "sum-body" });
      if (ch.people && ch.people.length) {
        body.append(el("p", { class: "sum-people" }, `${t("people")}: ${ch.people.map((p) => `${p.name} (${p.messages})`).join(", ")}`));
      }
      const part = (key, items, render, hint) => {
        if (!items || !items.length) return;
        body.append(el("div", { class: "sum-part" }, el("h3", {}, t(key)), hint ? el("p", { class: "hint-line" }, hint) : null, sumList(items, render)));
      };
      part("threads", ch.threads, (th) => [
        metaRow({ sender: (th.people || []).join(", ") }, el("span", {}, t("messagesN", th.messages))),
        el("p", { class: "snippet" }, (th.root && th.root.text) || ""),
      ]);
      part("questions", ch.questions, msgLine);
      part("decisions", ch.decisions, msgLine, t("decisionsNote"));
      part("requests", ch.tasks, msgLine);
      part("links", ch.links, (l) => [metaRow({ sender: l.sender, time: l.time }),
        /^https?:\/\//.test(l.url) ? el("a", { href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.url) : el("span", {}, l.url)]);
      part("files", ch.files, (f) => [metaRow({ sender: f.sender, time: f.time }), el("span", { class: "file-chip" }, f.name || "")]);
      return el("details", { class: "chat-sum", open: i === 0 },
        el("summary", {}, el("span", { class: "name" }, ch.title), el("span", { class: "count" }, t("messagesN", ch.messages)),
          ch.stale ? el("span", { class: "tag" }, t("stale")) : null),
        body);
    });
    const quiet = c.quiet && c.quiet.length ? el("p", { class: "quiet-line" }, `${t("quiet")}: ${c.quiet.join(", ")}`) : null;
    const note = c.stale && c.stale.length ? el("p", { class: "card-note" }, t("staleNote", c.stale.join(", "))) : null;
    return el("section", { class: "card" }, head, note, ...parts, quiet);
  }

  function taskRow(task) {
    const row = el("li", { class: task.status === "open" ? "row task" : "row task done" });
    const btn = el("button", { class: "btn", type: "button" }, task.status === "open" ? t("markDone") : t("undo"));
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const next = task.status === "open" ? "done" : "open";
        const res = await api(`/api/tasks/${task.id}`, { status: next });
        task.status = res.task.status;
        row.classList.toggle("done", task.status !== "open");
        btn.textContent = task.status === "open" ? t("markDone") : t("undo");
      } catch (e) {
        btn.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    });
    row.append(
      el("div", { class: "task-info" },
        el("p", { class: "task-text" }, task.text),
        metaRow(task, [
          task.for_me ? el("span", { class: "mine-tag" }, t("forYou")) : null,
          task.due ? el("span", { class: "due" }, `${t("due")}: ${task.due}`) : null,
        ])),
      btn);
    return row;
  }

  function tasksCard(c) {
    return el("section", { class: "card" },
      el("header", { class: "card-head" }, el("h2", {}, t("yourTasks")), el("span", { class: "count" }, t("tasksN", c.items.length))),
      el("ul", { class: "rows" }, c.items.map(taskRow)));
  }

  function chatsCard(c) {
    return el("section", { class: "card" },
      el("header", { class: "card-head" }, el("h2", {}, t("allChats")), el("span", { class: "count" }, t("chatsN", c.items.length))),
      el("ul", { class: "rows" }, c.items.map((ch) => el("li", { class: "row" },
        el("p", { class: "who" }, ch.title),
        el("div", { class: "row-meta" }, el("span", { class: ch.stale ? "fresh old" : "fresh" },
          `${t("messagesN", ch.messages)}, ${t("updated")} ${relTime(ch.synced_at)}`))))));
  }

  function examplesCard(c) {
    return el("ul", { class: "examples" }, (c.items || []).map((q) =>
      el("li", {}, el("button", { class: "chip", type: "button", onclick: () => ask(q) }, q))));
  }

  function card(c) {
    switch (c.type) {
      case "hits": return hitsCard(c);
      case "summary": return summaryCard(c);
      case "tasks": return tasksCard(c);
      case "chats": return chatsCard(c);
      case "examples": return examplesCard(c);
      default: return null;
    }
  }

  // ------------------------------------------------------------ messages

  function userMsg(text) {
    return el("div", { class: "msg msg-user" }, text);
  }

  function botMsg(mode) {
    const label = el("span", {}, `TG Kit · ${mode === "claude" ? t("modeClaude") : t("modeOffline")}`);
    const node = el("article", { class: "msg msg-bot" },
      el("div", { class: "msg-meta" }, el("img", { class: "logo", src: "/static/icon.svg", alt: "" }), label));
    const body = el("div", { class: "msg-body" });
    node.append(body);
    return {
      node, body, steps: null, stepItems: {},
      setMode(m) { label.textContent = `TG Kit · ${m === "claude" ? t("modeClaude") : t("modeOffline")}`; },
      pending(text) { body.className = "msg-body pending"; body.textContent = text; },
      stream(text) {
        if (!body.classList.contains("streaming")) { body.className = "msg-body streaming"; body.textContent = ""; }
        body.append(text);
      },
      final(text) { body.className = "msg-body"; body.replaceChildren(markdown(text)); },
      step(id, command, ok) {
        if (!this.steps) {
          this.stepsSummary = el("summary", {});
          this.stepsList = el("ol");
          this.steps = el("details", { class: "steps" }, this.stepsSummary, this.stepsList);
          node.insertBefore(this.steps, body);
        }
        let li = this.stepItems[id];
        if (!li) {
          li = el("li", {}, el("code", {}, command || ""), el("span", { class: "step-state" }));
          this.stepItems[id] = li;
          this.stepsList.append(li);
        }
        const state = li.querySelector(".step-state");
        if (ok === true) { state.className = "step-state ok"; state.textContent = t("stepOk"); }
        if (ok === false) { state.className = "step-state bad"; state.textContent = t("stepBad"); }
        this.stepsSummary.textContent = t("steps", Object.keys(this.stepItems).length);
      },
      cards(list) { for (const c of list || []) { const n = card(c); if (n) node.append(n); } },
      notice(text) { node.insertBefore(el("p", { class: "notice" }, text), body.nextSibling); },
      error(ev) { node.append(errorBox(ev)); },
    };
  }

  function errorBox(ev) {
    const box = el("div", { class: "alert", role: "alert" }, el("strong", {}, ev.message || t("errGeneric")),
      ev.hint ? el("p", {}, ev.hint) : null);
    if (ev.needs === "setup") box.append(el("button", { class: "btn btn-primary", type: "button", onclick: openSetup }, t("connect")));
    return box;
  }

  function scrollDown() {
    ui.scroller.scrollTop = ui.scroller.scrollHeight;
  }

  function renderTurn(turn) {
    if (turn.role === "user") return userMsg(turn.text || "");
    const b = botMsg(turn.mode);
    (turn.steps || []).forEach((s, i) => b.step(`s${i}`, s.command, s.ok));
    if (turn.error) b.error({ message: turn.text });
    else b.final(turn.text || "");
    if (turn.notice) b.notice(turn.notice);
    b.cards(turn.cards);
    return b.node;
  }

  async function openConversation(id) {
    if (app.busy) return;
    try {
      const conv = await api(`/api/conversations/${encodeURIComponent(id)}`);
      app.conv = { id: conv.id, title: conv.title };
      ui.title.textContent = conv.title;
      ui.thread.replaceChildren(...(conv.turns || []).map(renderTurn));
      showEmpty(false);
      renderLists();
      closeSide();
      requestAnimationFrame(scrollDown);
    } catch (e) {
      newConversation();
    }
  }

  function newConversation() {
    if (app.busy) stop();
    app.conv = null;
    ui.title.textContent = t("newChat");
    ui.thread.replaceChildren();
    showEmpty(true);
    renderLists();
    closeSide();
    ui.input.focus();
  }

  async function removeConversation(id) {
    try { await api(`/api/conversations/${encodeURIComponent(id)}/archive`, {}); } catch (e) { return; }
    if (app.conv && app.conv.id === id) newConversation();
    refresh();
  }

  // ------------------------------------------------------------ asking

  async function ask(text) {
    text = (text || "").trim();
    if (!text || app.busy) return;
    showEmpty(false);
    ui.input.value = "";
    autosize();
    ui.thread.append(userMsg(text));
    const claudeFirst = app.claude && app.mode !== "offline";
    const b = botMsg(claudeFirst ? "claude" : "offline");
    b.pending(t("thinking"));
    ui.thread.append(b.node);
    scrollDown();
    setBusy(true);
    app.ctrl = new AbortController();
    let action = null;
    try {
      await stream("/api/ask", { text, conversation: app.conv ? app.conv.id : null, mode: app.mode }, (ev) => {
        switch (ev.type) {
          case "conversation":
            app.conv = { id: ev.id, title: ev.title };
            ui.title.textContent = ev.title;
            break;
          case "mode": b.setMode(ev.mode); break;
          case "token": b.stream(ev.text); break;
          case "step": b.step(ev.id, ev.command, null); break;
          case "step_done": b.step(ev.id, null, ev.ok); break;
          case "cards": b.cards(ev.cards); break;
          case "notice": b.pending(t("thinking")); b.notice(ev.text); break;
          case "error": b.body.replaceChildren(); b.body.className = "msg-body"; b.error(ev); break;
          case "done": b.final(ev.text || ""); action = ev.action || null; break;
          default: break;
        }
        scrollDown();
      }, app.ctrl.signal);
    } catch (e) {
      if (e.name === "AbortError") b.final(t("stopped"));
      else { b.body.replaceChildren(); b.error({ message: e.message, hint: "" }); }
    } finally {
      setBusy(false);
      app.ctrl = null;
      refresh().catch(() => {});
      ui.input.focus();
    }
    if (action === "sync") sync();
  }

  function stop() {
    if (app.ctrl) app.ctrl.abort();
  }

  // ------------------------------------------------------------ sync & setup

  async function sync() {
    if (app.syncing) return;
    app.syncing = true;
    ui.syncBtn.disabled = true;
    ui.syncProgress.hidden = false;
    ui.syncProgress.classList.add("indeterminate");
    ui.syncStatus.textContent = t("syncing");
    let result = null;
    try {
      await stream("/api/sync", {}, (ev) => {
        if (ev.type === "progress") {
          if (ev.chats) {
            ui.syncProgress.classList.remove("indeterminate");
            ui.syncBar.style.width = `${Math.max(6, Math.round((100 * ev.done) / ev.chats))}%`;
          }
          ui.syncStatus.textContent = ev.line.trim();
        } else if (ev.type === "done" || ev.type === "error") {
          result = ev;
        }
      });
    } catch (e) {
      result = { type: "error", message: e.message };
    } finally {
      app.syncing = false;
      ui.syncBtn.disabled = false;
      ui.syncProgress.hidden = true;
      ui.syncBar.style.width = "6%";
    }
    ui.syncStatus.replaceChildren();
    if (!result || result.type === "error") {
      ui.syncStatus.textContent = (result && result.message) || t("syncFail");
    } else if (result.needs === "setup") {
      ui.syncStatus.append(`${t("notConnected")}. `, el("button", { class: "link-btn", type: "button", onclick: openSetup }, t("connect")));
    } else {
      ui.syncStatus.textContent = result.code === 0 ? t("syncDone", result.added) : `${t("syncDone", result.added)} ${t("syncFail")}`;
    }
    refresh().catch(() => {});
  }

  async function openSetup() {
    try {
      const r = await api("/api/setup", {});
      ui.syncStatus.textContent = r.ok ? t("setupOpened") : (r.hint || t("errGeneric"));
    } catch (e) {
      ui.syncStatus.textContent = e.message;
    }
  }

  // ------------------------------------------------------------ input

  function autosize() {
    ui.input.style.height = "auto";
    ui.input.style.height = `${Math.min(ui.input.scrollHeight, 200)}px`;
  }

  function closeSide() { ui.shell.classList.remove("side-open"); }

  ui.composer.addEventListener("submit", (e) => { e.preventDefault(); ask(ui.input.value); });
  ui.send.addEventListener("click", () => { if (app.busy) stop(); });
  ui.input.addEventListener("input", autosize);
  ui.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(ui.input.value); }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newConversation(); }
    else if (e.key === "Escape") { if (app.busy) stop(); else closeSide(); }
  });
  ui.newChat.addEventListener("click", newConversation);
  ui.filter.addEventListener("input", renderLists);
  ui.syncBtn.addEventListener("click", sync);
  ui.setupBtn.addEventListener("click", openSetup);
  ui.menuBtn.addEventListener("click", () => ui.shell.classList.toggle("side-open"));
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => {
    app.mode = r.value;
    localStorage.setItem("tgkit-mode", app.mode);
    setMode();
  }));

  // Browser mode: tell the server the page is still open.
  setInterval(() => { api("/api/ping").catch(() => {}); }, 20000);

  applyStrings();
  setBusy(false);
  refresh().then(() => {
    const start = params.get("c");
    if (start) openConversation(start);
    else newConversation();
  }).catch((e) => {
    showEmpty(true);
    ui.syncStatus.textContent = e.message;
  });
})();
