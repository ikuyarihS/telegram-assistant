/* Telegram Assistant app: chat UI over the local API. Every piece of chat data is put on the
   page with textContent or text nodes, never parsed as HTML. */

/* The chat picker's rules, kept free of the page so tests can load them with node.
   A dialog is { id, name, kind: "group" | "channel" | "person", added, picked }. */
const TGPick = {
  BIG: 30,
  fold(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
  },
  // The dialogs the list shows for the "Find a chat" text and the kind chip.
  shown(dialogs, query, kind) {
    const q = TGPick.fold((query || "").trim());
    return dialogs.filter((d) => (!kind || kind === "all" || d.kind === kind) && (!q || TGPick.fold(d.name).includes(q)));
  },
  pickable(list) { return list.filter((d) => !d.added); },
  // Select all of these: "all" ticked, "none" ticked, or "mixed".
  state(list) {
    const p = TGPick.pickable(list);
    const n = p.filter((d) => d.picked).length;
    if (!n) return "none";
    return n === p.length ? "all" : "mixed";
  },
  // Pressing Select all: ticks every shown chat, or clears them when all are ticked already.
  toggleAll(list) {
    const on = TGPick.state(list) !== "all";
    TGPick.pickable(list).forEach((d) => { d.picked = on; });
    return on;
  },
  count(dialogs) {
    const p = TGPick.pickable(dialogs);
    return { picked: p.filter((d) => d.picked).length, total: p.length };
  },
  big(n) { return n > TGPick.BIG; },
};
if (typeof module === "object" && module.exports) module.exports = TGPick;

(() => {
  "use strict";
  if (typeof document === "undefined") return;

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
      connect: "Connect to Telegram", answersBy: "Answers", auto: "Claude when available", offline: "Telegram Assistant only",
      modeClaude: "Claude", modeOffline: "Offline",
      claudeOn: "Claude Code is on this computer: it plans each search and writes the answer.",
      claudeOff: "Claude Code is not on this computer: Telegram Assistant answers with its own rules.",
      offlineOn: "Telegram Assistant answers with its own rules, without Claude.",
      emptyTitle: "Ask about your Telegram chats",
      emptyBody: "Search messages, get a summary of what happened, or see your tasks. Type in Vietnamese or English, accents optional.",
      inputPh: "Ask about your chats", hint: "Enter to send · Shift+Enter for a new line · Esc to stop",
      send: "Send", stop: "Stop", thinking: "Looking through your chats...", stopped: "Stopped.",
      steps: (n) => `Ran ${n} Telegram Assistant ${plural(n, "command", "commands")}`, stepOk: "done", stepBad: "failed",
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
      notConnected: "Telegram Assistant is not connected to Telegram yet",
      notConnectedBody: "Connect once, right here: about 3 minutes, then pick the chats to follow.",
      setupOpened: "The setup window is open. Come back here when it is done.",
      errGeneric: "Something went wrong. Try again.", errOffline: "Telegram Assistant stopped responding. Close this window and open Telegram Assistant again.",
      wordsMatch: "Every word, in any order", messagesFor: (q) => `“${q}”`, menu: "Show conversations",
      updated: "updated", never: "never", you: "You", summaryOf: (l) => `Summary: ${l}`, yourTasks: "Tasks",
      allChats: "Your chats", file: "File", examplesHead: "Try asking",
      settings: "Settings", close: "Close", checking: "Checking...", secClaude: "Claude",
      installClaude: "Get Claude Code", loginClaude: "Open a window to log in", checkAgain: "Check again",
      model: "Model for answers", effort: "Effort", modelDefault: "Claude Code default", effortDefault: "Claude Code default",
      modelHelp: "Claude Code default is a good choice. Opus thinks more and uses more of your plan; Haiku is quickest and uses the least.",
      stMissing: "Claude Code is not on this computer. Telegram Assistant answers by itself until it is.",
      stNotLogged: "Claude Code is installed but not logged in.",
      stConnected: (acct, plan) => `Connected${acct ? ` as ${acct}` : ""}${plan ? ` (${plan} plan)` : ""}.`,
      stUnknown: "Claude Code is installed. Its login could not be checked here.",
      fixMissing: ["Press Get Claude Code and install it. It comes with a Claude Pro or Max plan.",
        "Press Open a window to log in, and log in with your Claude account.", "Press Check again."],
      fixNotLogged: ["Press Open a window to log in.", "Log in with your Claude account in the page that opens.",
        "Come back and press Check again."],
      loginOpened: "A window opened. Log in there, then press Check again.",
      secUsage: "Token use",
      usageWhat: "Only answers written by Claude use tokens. Search, summary, tasks and Get new messages run on this computer and use none.",
      today: "Today", month: "This month", thisConv: "This conversation",
      answersN: (n) => `${n} ${plural(n, "answer", "answers")}`,
      tokensN: (n) => `${n} tokens`,
      usageBreak: (i, o, c) => `${i} in, ${o} out, ${c} from cache`,
      costNote: (c) => `About $${c} this month: Claude Code's own estimate. On a Claude plan you are not billed per answer.`,
      limit: "Warn me after this many tokens a day", limitPh: "0 = no limit",
      hardStop: "When it is reached, answer without Claude for the rest of the day",
      secExport: "Saved files", exportDir: "Folder", useDefault: "Use the default folder", openFolder: "Open folder",
      exportHelp: "Save as file puts Word documents (and Excel, CSV or Markdown when you pick them) in this folder.",
      secAccount: "Telegram account",
      accountHelp: "To use another Telegram account, log out here, then press Connect to Telegram. Your messages on Telegram are never touched.",
      logout: "Log out of Telegram", logoutAsk: "Log out of Telegram on this computer?", keepLegend: "Your copied chats",
      keepData: "Keep them here (you can still search them)",
      backupData: "Move them to a backup folder, so Telegram Assistant starts empty (nothing is deleted)",
      logoutYes: "Log out", cancel: "Cancel", loggedInAs: (n) => `Logged in to Telegram as ${n}.`,
      notLoggedIn: "Not logged in to Telegram on this computer.", loggedOut: "Logged out of Telegram.",
      movedTo: (p) => `Your chats were moved to ${p}.`, saved: "Saved.",
      exportBtn: "Save as file", fmtDocx: "Word document", fmtXlsx: "Excel sheet", fmtCsv: "CSV table", fmtMd: "Markdown",
      saving: "Saving...", savedAs: (n) => `Saved ${n}`, showFolder: "Show in folder", saveFile: "Save file",
      openTg: "Open in Telegram",
      usageLine: (m, n, b) => `${m ? `${m}, ` : ""}${n} tokens (${b})`, costLine: (c) => `about $${c}, Claude Code's estimate`,
      secData: "Copied chats", mediaCap: "Keep attached files up to (MB)",
      mediaHelp: "Bigger files stay on Telegram only; the message still says they exist. Applies to new messages.",
      dataAt: "Stored in", addChats: "Add chats",
      logHelp: "When something goes wrong, Telegram Assistant writes the details in a log file. Copy diagnostics and send it to whoever gave you Telegram Assistant. It holds no messages, codes or passwords.",
      copyDiag: "Copy diagnostics", openLogs: "Open log folder", diagCopied: "Diagnostics copied. Paste them in a message.",
      suRetry: "Load the chats again", suSwitched: "This is another Telegram account: the chats of the earlier one were moved aside and come back when you log in to it again.",
      suTitle: "Connect to Telegram", suStep1: "Telegram codes", suStep2: "Log in", suStep3: "Pick chats",
      suCodesWhy: "Telegram Assistant reads your chats with two codes from Telegram. You make them once, on Telegram's own website.",
      suCodes1: "Press Open my.telegram.org and log in with your phone number. Telegram sends the code to your Telegram app.",
      suCodes2: "Click API development tools. If it asks, type any App title (My Assistant) and Short name (tgkit), then Create application.",
      suCodes3: "Copy App api_id and App api_hash into the boxes below.",
      suOpenSite: "Open my.telegram.org", suCodesSafe: "The codes stay on this computer. Any Telegram account can use them.",
      suNext: "Next",
      suPhoneWhy: "Type the phone number of your Telegram account. Telegram sends a login code to your Telegram app.",
      suPhone: "Phone number, with country code", suSendCode: "Send code", suChangeCodes: "Change the Telegram codes",
      suCodeWhy: "Telegram sent a login code to your Telegram app (on your phone or computer). Type it here.",
      suCode: "Login code", suCodeSafe: "Never give this code to anyone. Telegram Assistant uses it only to log in on this computer.",
      suLogin: "Log in", suResend: "Send a new code",
      suPwWhy: "Your account has two-step verification. Type the extra password you set in Telegram.",
      suPw: "Two-step password",
      suChatsWhy: "Tick the chats Telegram Assistant should copy to this computer. Only these are searched and summarized.",
      suFilter: "Find a chat", suHow: "How much history",
      suRecent: "The last 1000 messages of each chat (quick)", suAll: "Everything (can take a while for big groups)",
      suAdd: "Add the ticked chats", suFinish: "Done", suTerminal: "Use the setup window in a terminal instead",
      suWorking: "Talking to Telegram...", suLoading: "Loading your chats...", suAdding: "Copying the chats. Big groups take a while...",
      suWho: (n) => `Logged in as ${n}.`,
      suParkedAt: (p) => `This is another Telegram account: the chats of the earlier one were moved to ${p}. Nothing was deleted.`,
      suRestored: "The chats this account had here before are back.",suAdded: (n) => `Added ${n} ${plural(n, "chat", "chats")}. Ask away, or add more.`,
      suNoneAdded: "No chat was added.", suPickOne: "Tick at least one chat.", suAlready: "already added",
      suPartial: "copied only in part: tick to copy again",
      suNoChats: "No chats found.", kindGroup: "group", kindChannel: "channel", kindUser: "person",
      suShowKind: "Show", suKindAll: "All", suKindGroups: "Groups", suKindChannels: "Channels", suKindPeople: "People",
      suPickAll: "Select all", suPickShown: (n) => `Select all ${n} shown`,
      suCount: (n, m) => `${n} of ${m} selected`,
      suBig: (n) => `${n} chats ticked. Copying that many the first time can take a while. Nothing starts until you press Add the ticked chats.`,
      openChatTg: "Open the chat in Telegram",
      openChatTip: "Telegram has no links to single messages of private chats and small groups, so this opens the chat.",
      findTg: (n) => `Open Telegram and find ${n}`, copyName: "Copy name", nameCopied: (n) => `Copied: ${n}`,
      findTgTip: "This chat has no Telegram link. Copy its name and paste it in Telegram's search.",
      suStep0: "Data folder",
      suFolderWhy: "Telegram Assistant keeps a copy of the chats you pick on this computer. Choose where.",
      suKeep: (p) => `Keep them in ${p}`, suOther: "Use another folder",
      suFolderPath: "Full path of the folder",
      suFolderHelp: "For example D:\\Telegram Assistant. Telegram Assistant makes the folder if it is not there yet.",
      progWaiting: "Waiting", progCopying: (n) => `Copying: ${n} ${plural(n, "message", "messages")} so far`,
      progDone: (n) => `Done: ${n} ${plural(n, "message", "messages")}`, progAlready: "Already in Telegram Assistant",
      progPartial: (n) => `Stopped early: only ${n} ${plural(n, "message", "messages")} copied, older ones are missing. Check your internet connection, then press Retry.`,
      progStopped: "Stopped", progRetry: "Retry", progList: "Copying the ticked chats",
      suAddedSome: (n, f) => `Added ${n} ${plural(n, "chat", "chats")}. ${f} could not be fully copied: see the list, and press Retry there.`,
      suNoneCopied: (f) => `${f} ${plural(f, "chat", "chats")} could not be fully copied: see the list, and press Retry there.`,
      stopRetry: "Stop and retry", stopping: "Stopping...",
      lockStuck: (m) => `Getting new messages has made no progress for ${m} minutes.`,
      lockGone: "The program that was getting new messages ended without finishing.",
      lockBusy: "Another Telegram Assistant window or terminal is getting new messages for this data folder.",
      switchedTo: (n) => `This computer is logged in to ${n}: Telegram Assistant now shows that account's chats.`,
      todayBtn: "What do I need to do today?", todayAsk: "What do I need to do today?",
      todayHead: "Today", itemsN: (n) => `${n} ${plural(n, "item", "items")}`,
      todayGroup_tasks: "Your open tasks", todayGroup_asks: "Waiting for your answer",
      todayGroup_today: "Mentioned for today", andMore: (n) => `And ${n} more.`,
      stopBtn: "Stop", syncStopping: "Stopping. Everything already saved is kept.",
      syncStopped: "Stopped. What was saved is kept; press Get new messages to get the rest.",
      progDoneFiles: (n, f) => `Done: ${n} ${plural(n, "message", "messages")}, ${f} ${plural(f, "file", "files")} still downloading`,
      progStoppedSaved: (n) => `Stopped: ${n} ${plural(n, "message", "messages")} saved, older ones not copied yet. Press Retry to copy the rest.`,
      filesLabel: "Files", filesSmall: (mb) => `Text and small files (up to ${mb} MB)`,
      filesNone: "Text only (fastest)", filesAll: "Everything (can take long)",
      filesHelp: "Messages are saved first and can be searched at once; files download after them.",
      filesLater: (f) => `${f} ${plural(f, "file", "files")} not downloaded yet, the next Get new messages gets ${f === 1 ? "it" : "them"}.`,
      copyRunning: "Copying the chats you ticked. Settings, Add chats shows each chat.",
      copyEnded: "The copy of the ticked chats has ended. Settings, Add chats shows each chat.",
      suListLater: "Your chats show here again when the copy has ended.",
    },
    vi: {
      newChat: "Cuộc trò chuyện mới", filter: "Lọc cuộc trò chuyện và nhóm", conversations: "Cuộc trò chuyện",
      chats: "Nhóm của bạn", noConvs: "Câu hỏi của bạn sẽ được lưu ở đây.", noChats: "Chưa có nhóm nào.",
      noMatch: "Không có mục nào khớp.",
      sync: "Lấy tin mới", syncing: "Đang lấy tin mới...",
      syncDone: (n) => (n ? `Có ${n} tin nhắn mới.` : "Đã cập nhật đầy đủ."),
      syncFail: "Một số nhóm chưa cập nhật được.", syncBusy: "Đang có một lượt lấy tin khác.",
      connect: "Kết nối Telegram", answersBy: "Trả lời bằng", auto: "Claude nếu có", offline: "Chỉ Telegram Assistant",
      modeClaude: "Claude", modeOffline: "Ngoại tuyến",
      claudeOn: "Máy này có Claude Code: Claude lên kế hoạch tìm kiếm và viết câu trả lời.",
      claudeOff: "Máy này chưa có Claude Code: Telegram Assistant tự trả lời theo quy tắc có sẵn.",
      offlineOn: "Telegram Assistant tự trả lời theo quy tắc có sẵn, không dùng Claude.",
      emptyTitle: "Hỏi về các nhóm Telegram của bạn",
      emptyBody: "Tìm tin nhắn, tóm tắt những gì đã diễn ra, hoặc xem việc cần làm. Gõ tiếng Việt hay tiếng Anh, có dấu hay không dấu đều được.",
      inputPh: "Hỏi về các nhóm của bạn", hint: "Enter để gửi · Shift+Enter để xuống dòng · Esc để dừng",
      send: "Gửi", stop: "Dừng", thinking: "Đang xem các nhóm của bạn...", stopped: "Đã dừng.",
      steps: (n) => `Đã chạy ${n} lệnh Telegram Assistant`, stepOk: "xong", stepBad: "lỗi",
      showCtx: "Xem cả đoạn", hideCtx: "Ẩn đoạn",
      messagesN: (n) => `${n} tin nhắn`, tasksN: (n) => `${n} việc`, chatsN: (n) => `${n} nhóm`,
      markDone: "Xong", undo: "Hoàn tác", forYou: "Của bạn", due: "Hạn",
      stale: "Có thể đã cũ", staleNote: (names) => `Chưa cập nhật hơn 12 giờ: ${names}. Bấm Lấy tin mới để cập nhật.`,
      people: "Người nhắn", threads: "Trao đổi nhiều nhất", questions: "Câu hỏi", decisions: "Có thể đã chốt",
      decisionsNote: "Xem tin được trả lời trước khi coi là đã đồng ý.", requests: "Yêu cầu và việc cần làm",
      links: "Liên kết", files: "Tệp", replyingTo: "Trả lời", notLocal: "một tin không có trong bản sao",
      quiet: "Không có tin trong khoảng này", remove: "Xóa cuộc trò chuyện", in: "trong",
      notConnected: "Telegram Assistant chưa kết nối với Telegram",
      notConnectedBody: "Kết nối một lần ngay tại đây: khoảng 3 phút, rồi chọn các nhóm cần theo dõi.",
      setupOpened: "Cửa sổ cài đặt đã mở. Xong thì quay lại đây.",
      errGeneric: "Có lỗi xảy ra. Thử lại nhé.", errOffline: "Telegram Assistant không phản hồi. Đóng cửa sổ này và mở lại Telegram Assistant.",
      wordsMatch: "Đủ các từ, không theo thứ tự", messagesFor: (q) => `“${q}”`, menu: "Hiện danh sách",
      updated: "cập nhật", never: "chưa bao giờ", you: "Bạn", summaryOf: (l) => `Tóm tắt: ${l}`, yourTasks: "Việc cần làm",
      allChats: "Nhóm của bạn", file: "Tệp", examplesHead: "Thử hỏi",
      settings: "Cài đặt", close: "Đóng", checking: "Đang kiểm tra...", secClaude: "Claude",
      installClaude: "Tải Claude Code", loginClaude: "Mở cửa sổ đăng nhập", checkAgain: "Kiểm tra lại",
      model: "Mô hình trả lời", effort: "Mức suy nghĩ", modelDefault: "Mặc định của Claude Code", effortDefault: "Mặc định của Claude Code",
      modelHelp: "Mặc định của Claude Code là lựa chọn tốt. Opus suy nghĩ kỹ hơn và dùng nhiều hạn mức hơn; Haiku nhanh nhất và dùng ít nhất.",
      stMissing: "Máy này chưa có Claude Code. Trong lúc đó Telegram Assistant tự trả lời.",
      stNotLogged: "Đã cài Claude Code nhưng chưa đăng nhập.",
      stConnected: (acct, plan) => `Đã kết nối${acct ? ` với ${acct}` : ""}${plan ? ` (gói ${plan})` : ""}.`,
      stUnknown: "Đã cài Claude Code. Không kiểm tra được trạng thái đăng nhập ở đây.",
      fixMissing: ["Bấm Tải Claude Code và cài đặt. Claude Code đi kèm gói Claude Pro hoặc Max.",
        "Bấm Mở cửa sổ đăng nhập và đăng nhập tài khoản Claude.", "Bấm Kiểm tra lại."],
      fixNotLogged: ["Bấm Mở cửa sổ đăng nhập.", "Đăng nhập tài khoản Claude ở trang vừa mở.", "Quay lại đây và bấm Kiểm tra lại."],
      loginOpened: "Một cửa sổ đã mở. Đăng nhập ở đó rồi bấm Kiểm tra lại.",
      secUsage: "Mức dùng token",
      usageWhat: "Chỉ câu trả lời do Claude viết mới dùng token. Tìm kiếm, tóm tắt, việc cần làm và Lấy tin mới chạy trên máy này, không dùng token.",
      today: "Hôm nay", month: "Tháng này", thisConv: "Cuộc trò chuyện này",
      answersN: (n) => `${n} câu trả lời`,
      tokensN: (n) => `${n} token`,
      usageBreak: (i, o, c) => `${i} vào, ${o} ra, ${c} từ bộ nhớ đệm`,
      costNote: (c) => `Khoảng $${c} trong tháng: ước tính của Claude Code. Với gói Claude, bạn không bị tính tiền theo từng câu trả lời.`,
      limit: "Cảnh báo khi dùng quá số token này mỗi ngày", limitPh: "0 = không giới hạn",
      hardStop: "Khi đạt mức này, trả lời không dùng Claude đến hết ngày",
      secExport: "Tệp đã lưu", exportDir: "Thư mục", useDefault: "Dùng thư mục mặc định", openFolder: "Mở thư mục",
      exportHelp: "Lưu thành tệp sẽ đặt tài liệu Word (và Excel, CSV hay Markdown khi bạn chọn) vào thư mục này.",
      secAccount: "Tài khoản Telegram",
      accountHelp: "Để dùng tài khoản Telegram khác, đăng xuất ở đây rồi bấm Kết nối Telegram. Tin nhắn trên Telegram không bị ảnh hưởng.",
      logout: "Đăng xuất Telegram", logoutAsk: "Đăng xuất Telegram trên máy này?", keepLegend: "Bản sao các nhóm",
      keepData: "Giữ lại ở đây (vẫn tìm kiếm được)",
      backupData: "Chuyển vào thư mục sao lưu để Telegram Assistant bắt đầu trống (không xóa gì)",
      logoutYes: "Đăng xuất", cancel: "Hủy", loggedInAs: (n) => `Đang đăng nhập Telegram với ${n}.`,
      notLoggedIn: "Máy này chưa đăng nhập Telegram.", loggedOut: "Đã đăng xuất Telegram.",
      movedTo: (p) => `Các nhóm đã được chuyển vào ${p}.`, saved: "Đã lưu.",
      exportBtn: "Lưu thành tệp", fmtDocx: "Tài liệu Word", fmtXlsx: "Bảng Excel", fmtCsv: "Bảng CSV", fmtMd: "Markdown",
      saving: "Đang lưu...", savedAs: (n) => `Đã lưu ${n}`, showFolder: "Mở thư mục", saveFile: "Lưu tệp",
      openTg: "Mở trên Telegram",
      usageLine: (m, n, b) => `${m ? `${m}, ` : ""}${n} token (${b})`, costLine: (c) => `khoảng $${c}, ước tính của Claude Code`,
      secData: "Bản sao các nhóm", mediaCap: "Giữ tệp đính kèm đến (MB)",
      mediaHelp: "Tệp lớn hơn chỉ nằm trên Telegram; tin nhắn vẫn ghi là có tệp. Áp dụng cho tin mới.",
      dataAt: "Lưu tại", addChats: "Thêm nhóm",
      logHelp: "Khi có lỗi, Telegram Assistant ghi chi tiết vào một tệp nhật ký. Bấm Chép thông tin chẩn đoán rồi gửi cho người đã đưa bạn Telegram Assistant. Trong đó không có tin nhắn, mã hay mật khẩu.",
      copyDiag: "Chép thông tin chẩn đoán", openLogs: "Mở thư mục nhật ký", diagCopied: "Đã chép thông tin chẩn đoán. Dán vào tin nhắn để gửi.",
      suRetry: "Tải lại danh sách nhóm", suSwitched: "Đây là tài khoản Telegram khác: các nhóm của tài khoản trước đã được cất riêng và sẽ trở lại khi bạn đăng nhập lại tài khoản đó.",
      suTitle: "Kết nối Telegram", suStep1: "Mã Telegram", suStep2: "Đăng nhập", suStep3: "Chọn nhóm",
      suCodesWhy: "Telegram Assistant đọc các nhóm của bạn bằng hai mã của Telegram. Bạn tạo chúng một lần trên trang web của Telegram.",
      suCodes1: "Bấm Mở my.telegram.org và đăng nhập bằng số điện thoại. Telegram gửi mã vào ứng dụng Telegram của bạn.",
      suCodes2: "Bấm API development tools. Nếu được hỏi, gõ App title bất kỳ (My Assistant) và Short name (tgkit), rồi bấm Create application.",
      suCodes3: "Chép App api_id và App api_hash vào hai ô bên dưới.",
      suOpenSite: "Mở my.telegram.org", suCodesSafe: "Hai mã này chỉ nằm trên máy này. Tài khoản Telegram nào cũng dùng được.",
      suNext: "Tiếp",
      suPhoneWhy: "Gõ số điện thoại của tài khoản Telegram. Telegram sẽ gửi mã đăng nhập vào ứng dụng Telegram của bạn.",
      suPhone: "Số điện thoại, có mã quốc gia", suSendCode: "Gửi mã", suChangeCodes: "Đổi mã Telegram",
      suCodeWhy: "Telegram đã gửi mã đăng nhập vào ứng dụng Telegram (trên điện thoại hoặc máy tính). Gõ mã đó vào đây.",
      suCode: "Mã đăng nhập", suCodeSafe: "Đừng đưa mã này cho ai. Telegram Assistant chỉ dùng nó để đăng nhập trên máy này.",
      suLogin: "Đăng nhập", suResend: "Gửi mã mới",
      suPwWhy: "Tài khoản của bạn bật xác minh hai bước. Gõ mật khẩu phụ bạn đã đặt trong Telegram.",
      suPw: "Mật khẩu hai bước",
      suChatsWhy: "Đánh dấu các nhóm Telegram Assistant sẽ sao chép về máy này. Chỉ những nhóm này được tìm kiếm và tóm tắt.",
      suFilter: "Tìm nhóm", suHow: "Lấy bao nhiêu tin cũ",
      suRecent: "1000 tin gần nhất của mỗi nhóm (nhanh)", suAll: "Tất cả (nhóm lớn có thể mất khá lâu)",
      suAdd: "Thêm các nhóm đã chọn", suFinish: "Xong", suTerminal: "Dùng cửa sổ cài đặt trong terminal",
      suWorking: "Đang kết nối Telegram...", suLoading: "Đang tải danh sách nhóm...", suAdding: "Đang sao chép các nhóm. Nhóm lớn sẽ mất một lúc...",
      suWho: (n) => `Đang đăng nhập với ${n}.`,
      suParkedAt: (p) => `Đây là tài khoản Telegram khác: các nhóm của tài khoản trước đã được chuyển vào ${p}. Không có gì bị xóa.`,
      suRestored: "Các nhóm trước đây của tài khoản này đã trở lại.",suAdded: (n) => `Đã thêm ${n} nhóm. Hỏi ngay, hoặc thêm nhóm khác.`,
      suNoneAdded: "Chưa thêm nhóm nào.", suPickOne: "Đánh dấu ít nhất một nhóm.", suAlready: "đã thêm",
      suPartial: "mới sao chép một phần: đánh dấu để sao chép lại",
      suNoChats: "Không tìm thấy nhóm nào.", kindGroup: "nhóm", kindChannel: "kênh", kindUser: "người",
      suShowKind: "Hiện", suKindAll: "Tất cả", suKindGroups: "Nhóm", suKindChannels: "Kênh", suKindPeople: "Người",
      suPickAll: "Chọn tất cả", suPickShown: (n) => `Chọn cả ${n} mục đang hiện`,
      suCount: (n, m) => `Đã chọn ${n} trên ${m}`,
      suBig: (n) => `Đã chọn ${n} nhóm. Lần sao chép đầu với nhiều nhóm như vậy có thể mất khá lâu. Chỉ bắt đầu khi bạn bấm Thêm các nhóm đã chọn.`,
      openChatTg: "Mở cuộc trò chuyện trên Telegram",
      openChatTip: "Telegram không có liên kết tới từng tin trong chat riêng và nhóm nhỏ, nên liên kết này mở cả cuộc trò chuyện.",
      findTg: (n) => `Mở Telegram và tìm ${n}`, copyName: "Chép tên", nameCopied: (n) => `Đã chép: ${n}`,
      findTgTip: "Cuộc trò chuyện này không có liên kết Telegram. Chép tên rồi dán vào ô tìm kiếm của Telegram.",
      suStep0: "Thư mục dữ liệu",
      suFolderWhy: "Telegram Assistant giữ bản sao các nhóm bạn chọn trên máy này. Chọn nơi lưu.",
      suKeep: (p) => `Giữ ở ${p}`, suOther: "Dùng thư mục khác",
      suFolderPath: "Đường dẫn đầy đủ của thư mục",
      suFolderHelp: "Ví dụ D:\\Telegram Assistant. Telegram Assistant tự tạo thư mục nếu chưa có.",
      progWaiting: "Đang chờ", progCopying: (n) => `Đang sao chép: đã có ${n} tin`,
      progDone: (n) => `Xong: ${n} tin`, progAlready: "Đã có trong Telegram Assistant",
      progPartial: (n) => `Dừng giữa chừng: mới sao chép ${n} tin, còn thiếu tin cũ hơn. Kiểm tra mạng rồi bấm Thử lại.`,
      progStopped: "Đã dừng", progRetry: "Thử lại", progList: "Đang sao chép các nhóm đã chọn",
      suAddedSome: (n, f) => `Đã thêm ${n} nhóm. ${f} nhóm chưa sao chép đủ: xem danh sách và bấm Thử lại ở đó.`,
      suNoneCopied: (f) => `${f} nhóm chưa sao chép đủ: xem danh sách và bấm Thử lại ở đó.`,
      stopRetry: "Dừng và thử lại", stopping: "Đang dừng...",
      lockStuck: (m) => `Việc lấy tin mới đã không tiến triển trong ${m} phút.`,
      lockGone: "Chương trình đang lấy tin mới đã tắt khi chưa xong.",
      lockBusy: "Một cửa sổ Telegram Assistant hoặc cửa sổ lệnh khác đang lấy tin mới cho thư mục dữ liệu này.",
      switchedTo: (n) => `Máy này đang đăng nhập ${n}: Telegram Assistant giờ hiện các nhóm của tài khoản đó.`,
      todayBtn: "Hôm nay tôi cần làm gì?", todayAsk: "Hôm nay tôi cần làm gì?",
      todayHead: "Hôm nay", itemsN: (n) => `${n} mục`,
      todayGroup_tasks: "Việc của bạn", todayGroup_asks: "Đang chờ bạn trả lời",
      todayGroup_today: "Nhắc đến hôm nay", andMore: (n) => `Và ${n} mục nữa.`,
      stopBtn: "Dừng", syncStopping: "Đang dừng. Những gì đã lưu vẫn được giữ.",
      syncStopped: "Đã dừng. Những gì đã lưu vẫn được giữ; bấm Lấy tin mới để lấy phần còn lại.",
      progDoneFiles: (n, f) => `Xong: ${n} tin, còn ${f} tệp đang tải`,
      progStoppedSaved: (n) => `Đã dừng: đã lưu ${n} tin, tin cũ hơn chưa sao chép. Bấm Thử lại để sao chép tiếp.`,
      filesLabel: "Tệp", filesSmall: (mb) => `Tin nhắn và tệp nhỏ (đến ${mb} MB)`,
      filesNone: "Chỉ tin nhắn (nhanh nhất)", filesAll: "Tất cả (có thể lâu)",
      filesHelp: "Tin nhắn được lưu trước và tìm được ngay; tệp tải về sau.",
      filesLater: (f) => `Còn ${f} tệp chưa tải, lần Lấy tin mới sau sẽ tải.`,
      copyRunning: "Đang sao chép các nhóm bạn đã chọn. Xem từng nhóm trong Cài đặt, Thêm nhóm.",
      copyEnded: "Đã sao chép xong các nhóm bạn đã chọn. Xem từng nhóm trong Cài đặt, Thêm nhóm.",
      suListLater: "Danh sách nhóm sẽ hiện lại khi sao chép xong.",
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
    settingsBtn: $("settingsBtn"), settings: $("settings"), setClose: $("setClose"), setStatus: $("setStatus"),
    claudeDot: $("claudeDot"), claudeStateText: $("claudeStateText"), claudeSteps: $("claudeSteps"),
    claudeInstall: $("claudeInstall"), claudeLogin: $("claudeLogin"), claudeCheck: $("claudeCheck"),
    modelRow: $("modelRow"), modelPick: $("modelPick"), effortField: $("effortField"), effortPick: $("effortPick"),
    usageGrid: $("usageGrid"), usageCost: $("usageCost"), limitInput: $("limitInput"), hardStop: $("hardStop"),
    exportDir: $("exportDir"), exportDefault: $("exportDefault"), openExport: $("openExport"),
    accountLine: $("accountLine"), logoutBtn: $("logoutBtn"), connectBtn: $("connectBtn"),
    logoutConfirm: $("logoutConfirm"), logoutYes: $("logoutYes"), logoutNo: $("logoutNo"),
    convExport: $("convExport"), toast: $("toast"), toastText: $("toastText"), toastShow: $("toastShow"),
    toastClose: $("toastClose"),
    mediaCap: $("mediaCap"), dataPath: $("dataPath"), addChatsBtn: $("addChatsBtn"), openData: $("openData"),
    setupDlg: $("setupDlg"), suClose: $("suClose"), suSteps: $("suSteps"), suStatus: $("suStatus"),
    suCodes: $("suCodes"), suPhone: $("suPhone"), suCode: $("suCode"), suPassword: $("suPassword"), suChats: $("suChats"),
    suSite: $("suSite"), suApiId: $("suApiId"), suApiHash: $("suApiHash"), suPhoneIn: $("suPhoneIn"),
    suChangeCodes: $("suChangeCodes"), suCodeIn: $("suCodeIn"), suResend: $("suResend"), suPwIn: $("suPwIn"),
    suWho: $("suWho"), suFilter: $("suFilter"), suList: $("suList"), suAdd: $("suAdd"), suDone: $("suDone"),
    suTerminal: $("suTerminal"), suTools: $("suTools"), suKinds: $("suKinds"), suKindChannel: $("suKindChannel"),
    suPickAll: $("suPickAll"), suPickAllText: $("suPickAllText"), suCount: $("suCount"), suBigNote: $("suBigNote"),
    copyDiag: $("copyDiag"), openLogs: $("openLogs"),
    suFolder: $("suFolder"), suKeepText: $("suKeepText"), suFolderPathField: $("suFolderPathField"),
    suFolderPathIn: $("suFolderPathIn"), suProg: $("suProg"), suProgList: $("suProgList"), suStop: $("suStop"),
    lockNote: $("lockNote"), lockText: $("lockText"), lockStop: $("lockStop"),
    syncStop: $("syncStop"), todayBtn: $("todayBtn"), todayEmpty: $("todayEmpty"),
    suFilesSmallText: $("suFilesSmallText"), mediaMode: $("mediaMode"),
  };

  const app = {
    conv: null, convs: [], chats: [], claude: false, busy: false, ctrl: null, syncing: false, maxMedia: 50,
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

  // The server answers in the window's language. A problem the user can fix (a wrong
  // login code, a bad setting) comes back as 200 with user_error, so the browser
  // logs no failed request for it; it is still thrown here like any error.
  const HEADERS = { "X-TGKit-Token": TOKEN, "X-TGKit-Lang": LANG, "Content-Type": "application/json" };

  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: HEADERS,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw Object.assign(new Error(t("errOffline")), { data: {} });
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.user_error) throw Object.assign(new Error(data.message || t("errGeneric")), { data });
    return data;
  }

  async function stream(path, body, onEvent, signal) {
    const res = await fetch(path, {
      method: "POST",
      headers: HEADERS,
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
    showMediaCap(app.maxMedia);
  }

  // The Files choice names the size limit from the settings ("small" keeps files up to it).
  function showMediaCap(mb) {
    app.maxMedia = mb || app.maxMedia || 50;
    if (ui.suFilesSmallText) ui.suFilesSmallText.textContent = t("filesSmall", app.maxMedia);
    if (ui.mediaMode) {
      const keep = ui.mediaMode.value || "small";
      ui.mediaMode.replaceChildren(...["small", "none", "all"].map((m) =>
        el("option", { value: m }, m === "small" ? t("filesSmall", app.maxMedia) : m === "none" ? t("filesNone") : t("filesAll"))));
      ui.mediaMode.value = keep;
    }
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

  // ------------------------------------------------------------ files

  const num = (n) => new Intl.NumberFormat(LANG).format(n || 0);
  let toastTimer = null;
  let toastFile = null;

  function toast(text, path, bad) {
    ui.toastText.textContent = text;
    toastFile = path || null;
    ui.toastShow.hidden = !path;
    ui.toast.classList.toggle("bad", !!bad);
    ui.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { ui.toast.hidden = true; }, bad ? 12000 : 9000);
  }

  const FORMATS = [["docx", "fmtDocx"], ["xlsx", "fmtXlsx"], ["csv", "fmtCsv"], ["md", "fmtMd"]];

  // "Save as file": Word by default, other formats in the same small menu.
  function exportMenu(spec) {
    const menu = el("div", { class: "export-menu", role: "menu" });
    const box = el("details", { class: "export" },
      el("summary", { class: "btn btn-small" }, t("exportBtn")), menu);
    for (const [fmt, key] of FORMATS) {
      menu.append(el("button", {
        type: "button", role: "menuitem",
        onclick: async () => {
          box.open = false;
          toast(t("saving"));
          try {
            const r = await api("/api/export", { ...spec, format: fmt, lang: LANG });
            toast(t("savedAs", r.name), r.path);
          } catch (e) {
            toast(e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message, null, true);
          }
        },
      }, el("span", {}, t(key)), el("span", { class: "ext" }, `.${fmt}`)));
    }
    return box;
  }

  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.export[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  });

  function saveFileButton(folder, id) {
    return el("button", {
      class: "link-btn", type: "button",
      onclick: async () => {
        try {
          const r = await api("/api/save-file", { folder, id });
          toast(t("savedAs", r.name), r.path);
        } catch (e) {
          toast(e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message, null, true);
        }
      },
    }, t("saveFile"));
  }

  // The Telegram link of a message: the message itself in channels and supergroups;
  // private chats and basic groups have no message links, so theirs opens the chat
  // when it has a username; without one there is no link: { url: null, kind: "none" }.
  function linkFor(folder, id) {
    const chat = app.chats.find((c) => c.folder === folder);
    if (!chat) return null;
    if (!chat.link_tpl) return { url: null, kind: "none", name: chat.title };
    let url = chat.link_tpl;
    if (url.includes("{id}")) {
      if (id === undefined || id === null || id === "") {
        if (chat.link_kind !== "chat") return null;
        url = url.replace(/&message_id=\{id\}/, "");
      } else {
        url = url.replace("{id}", encodeURIComponent(id));
      }
    }
    return { url, kind: chat.link_kind };
  }

  // Link forms Telegram Desktop cannot open; cards saved by an earlier 0.2.3 build hold them.
  const DEAD_LINK = /^\s*tg:\/\/(user|openmessage)\b/i;

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const area = el("textarea", { class: "copy-buffer", readonly: true });
      area.value = text;
      document.body.append(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    }
  }

  // No link: say where to find the chat, with a button that copies its name.
  function findHint(name) {
    if (!name) return null;
    const btn = el("button", { class: "link-btn", type: "button" }, t("copyName"));
    btn.addEventListener("click", async () => {
      if (await copyText(name)) toast(t("nameCopied", name));
    });
    return el("span", { class: "tg-find", title: t("findTgTip") },
      el("span", { class: "tg-find-text" }, t("findTg", name)), btn);
  }

  function tgLink(link) {
    if (link && (DEAD_LINK.test(link.url || "") || (!link.url && link.kind === "none"))) return findHint(link.name);
    if (!link || !link.url) return null;
    const chatOnly = link.kind === "chat";
    return el("a", {
      class: "tg-link", href: link.url, target: "_blank", rel: "noopener noreferrer",
      title: chatOnly ? t("openChatTip") : null,
      onclick: (e) => {
        e.preventDefault();
        api("/api/open-link", { url: link.url })
          .then((r) => { if (r && r.note) toast(r.note); })
          .catch((err) => toast(err.data && err.data.hint ? `${err.message} ${err.data.hint}` : err.message, null, true));
      },
    }, chatOnly ? t("openChatTg") : t("openTg"));
  }

  // The link of a row: for a chat still in Telegram Assistant, its link as it is now (the same one
  // the export gives; a saved card can hold a link an earlier build made); else the
  // row's own link, never a form Telegram Desktop cannot open.
  function itemLink(item, folder, chatName) {
    const name = item.chat || chatName;
    const fresh = linkFor(item.folder || folder, item.id);
    if (fresh) return fresh.url ? fresh : { ...fresh, name: fresh.name || name };
    if (item.link && !DEAD_LINK.test(item.link)) return { url: item.link, kind: item.link_kind || "message" };
    if (item.link || item.link_kind === "none") return { url: null, kind: "none", name };
    return null;
  }

  function usageText(u, model) {
    if (!u) return "";
    const total = (u.input || 0) + (u.output || 0) + (u.cache_read || 0) + (u.cache_write || 0);
    let s = t("usageLine", model || "", num(total),
      t("usageBreak", num(u.input), num(u.output), num((u.cache_read || 0) + (u.cache_write || 0))));
    if (typeof u.cost_usd === "number") s += `, ${t("costLine", u.cost_usd.toFixed(u.cost_usd < 0.1 ? 3 : 2))}`;
    return s;
  }

  async function refresh() {
    const s = await api("/api/state");
    app.claude = s.claude;
    app.convs = s.conversations || [];
    app.chats = s.chats || [];
    ui.version.textContent = `v${s.version}`;
    ui.setupBox.hidden = !!app.chats.length;
    app.loggedIn = !!s.logged_in;
    ui.setupBtn.textContent = s.logged_in ? t("addChats") : t("connect");
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
    return [el("div", { class: "row-actions" }, btn, tgLink(itemLink(item))), box];
  }

  function hitsCard(c) {
    const head = el("header", { class: "card-head" },
      el("h2", {}, t("messagesFor", c.query || "")),
      el("span", { class: "count" }, c.match === "words" ? `${t("messagesN", c.total)}, ${t("wordsMatch")}` : t("messagesN", c.total)),
      exportMenu({ kind: "card", card: c }));
    const note = c.stale && c.stale.length ? el("p", { class: "card-note" }, t("staleNote", c.stale.join(", "))) : null;
    const rows = el("ul", { class: "rows" }, c.items.map((h) => el("li", { class: "row" },
      metaRow(h),
      h.text ? el("p", { class: "snippet" }, highlight(h.text, c.query)) : null,
      h.file ? el("div", { class: "file-row" },
        el("span", { class: "file-chip" }, `${t("file")}: `, highlight(h.file, c.query)),
        h.folder ? saveFileButton(h.folder, h.id) : null) : null,
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

  function msgLine(it, ch) {
    return [metaRow({ sender: it.sender, time: it.time }, [
      it.due ? el("span", { class: "due" }, `${t("due")}: ${it.due}`) : null,
      tgLink(itemLink(it, ch.folder, ch.title))]),
    el("p", { class: "snippet" }, it.text || ""), quoteLine(it)];
  }

  function summaryCard(c) {
    const head = el("header", { class: "card-head" },
      el("h2", {}, t("summaryOf", c.label || "")),
      el("span", { class: "count" }, `${t("messagesN", c.total)}, ${t("chatsN", c.chats.length)}`),
      exportMenu({ kind: "card", card: c }));
    const parts = c.chats.map((ch, i) => {
      const body = el("div", { class: "sum-body" });
      if (ch.people && ch.people.length) {
        body.append(el("p", { class: "sum-people" }, `${t("people")}: ${ch.people.map((p) => `${p.name} (${p.messages})`).join(", ")}`));
      }
      const part = (key, items, render, hint) => {
        if (!items || !items.length) return;
        body.append(el("div", { class: "sum-part" }, el("h3", {}, t(key)), hint ? el("p", { class: "hint-line" }, hint) : null, sumList(items, render)));
      };
      const tg = (it) => tgLink(itemLink(it || {}, ch.folder, ch.title));
      part("threads", ch.threads, (th) => [
        metaRow({ sender: (th.people || []).join(", ") }, [el("span", {}, t("messagesN", th.messages)), tg(th.root)]),
        el("p", { class: "snippet" }, (th.root && th.root.text) || ""),
      ]);
      const line = (it) => msgLine(it, ch);
      part("questions", ch.questions, line);
      part("decisions", ch.decisions, line, t("decisionsNote"));
      part("requests", ch.tasks, line);
      part("links", ch.links, (l) => [metaRow({ sender: l.sender, time: l.time }, tg(l)),
        /^https?:\/\//.test(l.url) ? el("a", { class: "sum-url", href: l.url, target: "_blank", rel: "noopener noreferrer" }, l.url) : el("span", { class: "sum-url" }, l.url)]);
      part("files", ch.files, (f) => [metaRow({ sender: f.sender, time: f.time }, tg(f)),
        el("div", { class: "file-row" }, el("span", { class: "file-chip" }, f.name || ""),
          f.path && ch.folder && f.id ? saveFileButton(ch.folder, f.id) : null)]);
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
          tgLink(linkFor(task.folder, task.msg_id)),
        ])),
      btn);
    return row;
  }

  function tasksCard(c) {
    return el("section", { class: "card" },
      el("header", { class: "card-head" }, el("h2", {}, t("yourTasks")), el("span", { class: "count" }, t("tasksN", c.items.length)),
        exportMenu({ kind: "card", card: c })),
      el("ul", { class: "rows" }, c.items.map(taskRow)));
  }

  function chatsCard(c) {
    return el("section", { class: "card" },
      el("header", { class: "card-head" }, el("h2", {}, t("allChats")), el("span", { class: "count" }, t("chatsN", c.items.length)),
        exportMenu({ kind: "card", card: c })),
      el("ul", { class: "rows" }, c.items.map((ch) => el("li", { class: "row" },
        el("p", { class: "who" }, ch.title),
        el("div", { class: "row-meta" }, el("span", { class: ch.stale ? "fresh old" : "fresh" },
          `${t("messagesN", ch.messages)}, ${t("updated")} ${relTime(ch.synced_at)}`))))));
  }

  function examplesCard(c) {
    return el("ul", { class: "examples" }, (c.items || []).map((q) =>
      el("li", {}, el("button", { class: "chip", type: "button", onclick: () => ask(q) }, q))));
  }

  // "What do I need to do today": three short groups, each item with who, where, when
  // and its Telegram link. The server cuts each group to a few items and says how many more.
  function todayCard(c) {
    const groups = (c.groups || []).filter((g) => g.total);
    const note = c.stale && c.stale.length ? el("p", { class: "card-note" }, t("staleNote", c.stale.join(", "))) : null;
    return el("section", { class: "card today-card" },
      el("header", { class: "card-head" }, el("h2", {}, t("todayHead")), el("span", { class: "count" }, t("itemsN", c.total || 0))),
      note,
      ...groups.map((g) => el("div", { class: "today-group" },
        el("h3", { class: "today-title" }, `${t("todayGroup_" + g.key)} (${g.total})`),
        el("ul", { class: "rows" }, (g.items || []).map((it) => el("li", { class: "row" },
          metaRow(it, [
            it.due ? el("span", { class: it.due_today ? "due due-today" : "due" }, `${t("due")}: ${it.due}`) : null,
            tgLink(itemLink(it, it.folder, it.chat))]),
          el("p", { class: "snippet" }, it.text || "")))),
        g.more ? el("p", { class: "quiet-line" }, t("andMore", g.more)) : null)));
  }

  function card(c) {
    switch (c.type) {
      case "today": return todayCard(c);
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
    const label = el("span", {}, `Telegram Assistant · ${mode === "claude" ? t("modeClaude") : t("modeOffline")}`);
    const node = el("article", { class: "msg msg-bot" },
      el("div", { class: "msg-meta" }, el("img", { class: "logo", src: "/static/icon.svg", alt: "" }), label));
    const body = el("div", { class: "msg-body" });
    node.append(body);
    return {
      node, body, steps: null, stepItems: {},
      setMode(m) { label.textContent = `Telegram Assistant · ${m === "claude" ? t("modeClaude") : t("modeOffline")}`; },
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
      usage(u, model) {
        const text = usageText(u, model);
        if (text) node.insertBefore(el("p", { class: "usage-line" }, text), body.nextSibling);
      },
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
    if (turn.usage || turn.model) b.usage(turn.usage || {}, turn.model);
    b.cards(turn.cards);
    return b.node;
  }

  function showConvExport() {
    ui.convExport.replaceChildren();
    ui.convExport.hidden = !app.conv;
    if (app.conv) ui.convExport.append(exportMenu({ kind: "conversation", conversation: app.conv.id }));
  }

  async function openConversation(id) {
    if (app.busy) return;
    try {
      const conv = await api(`/api/conversations/${encodeURIComponent(id)}`);
      app.conv = { id: conv.id, title: conv.title };
      ui.title.textContent = conv.title;
      ui.thread.replaceChildren(...(conv.turns || []).map(renderTurn));
      showConvExport();
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
    showConvExport();
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
            showConvExport();
            break;
          case "mode": b.setMode(ev.mode); break;
          case "token": b.stream(ev.text); break;
          case "step": b.step(ev.id, ev.command, null); break;
          case "step_done": b.step(ev.id, null, ev.ok); break;
          case "cards": b.cards(ev.cards); break;
          case "notice": b.pending(t("thinking")); b.notice(ev.text); break;
          case "error": b.body.replaceChildren(); b.body.className = "msg-body"; b.error(ev); break;
          case "done":
            b.final(ev.text || "");
            action = ev.action || null;
            if (ev.turn && (ev.turn.usage || ev.turn.model)) b.usage(ev.turn.usage || {}, ev.turn.model);
            break;
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

  // Another account's chats are never shown: after any switch the lists, the open
  // conversation and the search results start again from the logged-in account's data.
  function onAccountSwitch() {
    newConversation();
    refresh().catch(() => {});
  }

  // A sync lock held with no progress (or by a program that ended) is shown with
  // Stop and retry when it is this window's own task; otherwise in plain words.
  function showLock(lock) {
    if (!lock || !lock.held) { ui.lockNote.hidden = true; return; }
    const text = !lock.alive ? t("lockGone") : lock.stuck ? t("lockStuck", Math.max(1, Math.round((lock.idle || 0) / 60))) : t("lockBusy");
    ui.lockText.textContent = text;
    ui.lockStop.hidden = !(lock.stuck && lock.can_stop);
    ui.lockStop.disabled = false;
    ui.lockNote.hidden = !(lock.stuck || !lock.can_stop);
  }

  let lockTimer = null;
  function watchLock(on) {
    clearInterval(lockTimer);
    lockTimer = on ? setInterval(() => { api("/api/lock").then(showLock).catch(() => {}); }, 15000) : null;
  }

  ui.lockStop.addEventListener("click", async () => {
    ui.lockStop.disabled = true;
    ui.lockText.textContent = t("stopping");
    try {
      await api("/api/sync/stop", {});
      ui.lockNote.hidden = true;
      while (app.syncing) await new Promise((res) => setTimeout(res, 300));
      sync();
    } catch (e) {
      ui.lockText.textContent = e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message;
      ui.lockStop.hidden = true;
    }
  });

  async function sync() {
    if (app.syncing || following) return;
    app.syncing = true;
    ui.syncBtn.disabled = true;
    ui.syncProgress.hidden = false;
    ui.syncProgress.classList.add("indeterminate");
    ui.syncStatus.textContent = t("syncing");
    ui.lockNote.hidden = true;
    ui.syncStop.hidden = false;  // Stop is shown the whole time a sync runs
    ui.syncStop.disabled = false;
    watchLock(true);
    let result = null;
    let switched = false;
    try {
      await stream("/api/sync", {}, (ev) => {
        if (ev.type === "running") {
          ui.syncStop.hidden = false;
        } else if (ev.type === "switched") {
          switched = true;
          onAccountSwitch();
        } else if (ev.type === "progress") {
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
      watchLock(false);
      ui.syncStop.hidden = true;
      ui.syncBtn.disabled = false;
      ui.syncProgress.hidden = true;
      ui.syncBar.style.width = "6%";
    }
    syncResult(result, switched);
  }

  // What a sync ended with, on the sync bar (this page's own stream, or followed).
  function syncResult(result, switched) {
    ui.syncStatus.replaceChildren();
    if (result && result.lock) showLock(result.lock);
    if (!result || result.type === "error") {
      ui.syncStatus.textContent = (result && result.message) || t("syncFail");
    } else if (result.lock && result.lock.held) {
      ui.syncStatus.textContent = t("syncBusy");
    } else if (result.needs === "setup") {
      ui.syncStatus.append(`${t("notConnected")}. `, el("button", { class: "link-btn", type: "button", onclick: openSetup }, t("connect")));
    } else if (result.stopped) {
      ui.syncStatus.textContent = `${t("syncDone", result.added || 0)} ${t("syncStopped")}`;
    } else {
      ui.syncStatus.textContent = result.code === 0 ? t("syncDone", result.added) : `${t("syncDone", result.added)} ${t("syncFail")}`;
    }
    if (switched) onAccountSwitch();
    else refresh().catch(() => {});
  }

  // A page loaded while a sync or a copy of this window runs (first open, reload, a
  // reopened tab, the setup closed mid-copy) shows it on the sync bar with its Stop
  // until it ends: the Stop is on screen the whole time the task runs.
  let following = false;
  async function followRunning() {
    if (following || app.syncing) return;
    let r;
    try { r = await api("/api/running"); } catch (e) { return; }
    if (!r.sync.running && !r.copy) return;
    following = true;
    const kind = r.sync.running ? "sync" : "copy";
    const on = (x) => (kind === "sync" ? x.sync.running : x.copy);
    ui.syncBtn.disabled = true;
    ui.syncProgress.hidden = false;
    ui.syncProgress.classList.add("indeterminate");
    ui.lockNote.hidden = true;
    ui.syncStop.hidden = false;
    ui.syncStop.disabled = false;
    const show = (x) => {
      if (ui.syncStop.disabled) return;  // "Stopping..." stays until it has ended
      if (kind === "copy") { ui.syncStatus.textContent = t("copyRunning"); return; }
      ui.syncStatus.textContent = (x.sync.line || "").trim() || t("syncing");
      if (x.sync.chats && x.sync.done) {
        ui.syncProgress.classList.remove("indeterminate");
        ui.syncBar.style.width = `${Math.max(6, Math.round((100 * x.sync.done) / x.sync.chats))}%`;
      }
    };
    show(r);
    watchLock(true);
    try {
      while (on(r)) {
        await new Promise((res) => setTimeout(res, 1000));
        try { r = await api("/api/running"); } catch (e) { continue; }
        show(r);
      }
    } finally {
      following = false;
      watchLock(false);
      ui.syncStop.hidden = true;
      ui.syncBtn.disabled = false;
      ui.syncProgress.hidden = true;
      ui.syncBar.style.width = "6%";
    }
    if (kind === "sync" && r.sync.last) {
      syncResult(r.sync.last, !!r.sync.last.switched);
    } else if (kind === "sync") {
      ui.syncStatus.textContent = "";
      refresh().catch(() => {});
    } else {
      ui.syncStatus.textContent = t("copyEnded");
      refresh().catch(() => {});
    }
  }

  // Stop on the sync bar: ends this window's own sync or copy cleanly (/api/stop); every
  // chat already saved is kept and the next Get new messages gets the rest.
  ui.syncStop.addEventListener("click", async () => {
    ui.syncStop.disabled = true;
    ui.syncStatus.textContent = t("syncStopping");
    try {
      await api("/api/stop", {});
    } catch (e) {
      ui.syncStop.disabled = false;
      ui.syncStatus.textContent = e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message;
    }
  });

  // The fallback: `tgkit setup` in a terminal window.
  async function openTerminalSetup() {
    try {
      const r = await api("/api/setup", {});
      ui.syncStatus.textContent = r.ok ? t("setupOpened") : (r.hint || t("errGeneric"));
    } catch (e) {
      ui.syncStatus.textContent = e.message;
    }
  }

  // ------------------------------------------------------------ setup in the window

  const PANES = { folder: "suFolder", codes: "suCodes", phone: "suPhone", code: "suCode", password: "suPassword", chats: "suChats" };
  const STEP_OF = { folder: "folder", codes: "codes", phone: "login", code: "login", password: "login", chats: "chats" };
  let suDialogs = [];
  let suAddedAny = false;
  let suFolderDone = false;

  function suNote(text, bad) {
    ui.suStatus.textContent = text || "";
    ui.suStatus.classList.toggle("bad", !!bad);
  }

  function suError(e) {
    suNote(e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message, true);
  }

  function suShow(pane) {
    for (const [name, id] of Object.entries(PANES)) ui[id].hidden = name !== pane;
    const order = ["folder", "codes", "login", "chats"];
    const at = order.indexOf(STEP_OF[pane]);
    ui.suSteps.querySelectorAll("li").forEach((li, i) => {
      li.classList.toggle("done", i < at);
      li.classList.toggle("now", i === at);
      if (i === at) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    const first = ui[PANES[pane]].querySelector("input:not([type=radio])");
    if (first && pane !== "chats") setTimeout(() => first.focus(), 0);
    if (pane === "chats") loadDialogs();
  }

  // One request at a time: the pane's buttons wait for Telegram's answer.
  async function suRun(form, call) {
    const buttons = form.querySelectorAll("button");
    buttons.forEach((b) => { b.disabled = true; });
    suNote(t("suWorking"));
    try {
      const r = await call();
      suNote("");
      return r;
    } catch (e) {
      suError(e);
      return null;
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  function suAfterLogin(r) {
    if (!r) return;
    if (r.step === "code") suShow("code");
    else if (r.step === "password") suShow("password");
    else if (r.step === "done") {
      // only what really moved: nothing parked or restored, no switch note
      const notes = [];
      if (r.parked) notes.push(r.comes_back ? t("suSwitched") : t("suParkedAt", r.parked));
      if (r.restored) notes.push(t("suRestored"));
      ui.suWho.textContent = [t("suWho", (r.me && r.me.name) || ""), ...notes].join(" ");
      if (r.switched) onAccountSwitch(r);
      else refresh().catch(() => {});
      suShow("chats");
    }
  }

  async function openSetup(pane) {
    if (ui.settings.open) ui.settings.close();
    if (!ui.setupDlg.open) ui.setupDlg.showModal();
    suNote("");
    suAddedAny = false;
    try {
      const s = await api("/api/setup/state");
      if (typeof pane === "string") suShow(pane);
      else suRoute(s);
    } catch (e) {
      suShow("codes");
      suError(e);
    }
  }

  // The next pane for a setup state. A first setup starts with where to keep the data.
  function suRoute(s) {
    ui.suApiId.value = s.api_id || "";
    ui.suWho.textContent = s.logged_in && s.me ? t("suWho", s.me.name || "") : "";
    ui.suKeepText.textContent = t("suKeep", s.home || "");
    if (s.fresh && !s.home_fixed && !suFolderDone && !s.step) suShow("folder");
    else if (!s.has_codes) suShow("codes");
    else if (s.step === "code" || s.step === "password") suShow(s.step);
    else if (!s.logged_in) suShow("phone");
    else suShow("chats");
  }

  function suFolderChoice() {
    const other = (document.querySelector('input[name="suHome"]:checked') || {}).value === "other";
    ui.suFolderPathField.hidden = !other;
    ui.suFolderPathIn.required = other;
    if (other) setTimeout(() => ui.suFolderPathIn.focus(), 0);
  }

  function kindLabel(kind) {
    const k = (kind || "").toLowerCase();
    if (k.includes("channel")) return t("kindChannel");
    if (k.includes("user") || k.includes("person")) return t("kindUser");
    return t("kindGroup");
  }

  let suShown = [];
  function suKind() {
    return (document.querySelector('input[name="suKind"]:checked') || {}).value || "all";
  }

  // The bar above the list: Select all (ticked, clear or mixed) for the chats shown now,
  // the count of the whole pick, and a plain note when the pick is big.
  function syncPickBar() {
    ui.suTools.hidden = !suDialogs.length;
    const c = TGPick.count(suDialogs);
    const big = TGPick.big(c.picked);
    ui.suBigNote.hidden = !big;
    ui.suBigNote.textContent = big ? t("suBig", c.picked) : "";
    if (!suDialogs.length) return;
    const state = TGPick.state(suShown);
    const n = TGPick.pickable(suShown).length;
    ui.suPickAll.checked = state === "all" && n > 0;
    ui.suPickAll.indeterminate = state === "mixed";
    ui.suPickAll.disabled = !n;
    ui.suPickAllText.textContent = suShown.length < suDialogs.length ? t("suPickShown", n) : t("suPickAll");
    ui.suCount.textContent = t("suCount", c.picked, c.total);
  }

  function renderDialogs() {
    const channels = suDialogs.some((d) => d.kind === "channel");
    ui.suKindChannel.hidden = !channels;
    if (!channels && suKind() === "channel") ui.suKinds.querySelector('input[value="all"]').checked = true;
    suShown = TGPick.shown(suDialogs, ui.suFilter.value, suKind());
    syncPickBar();
    const shown = suShown;
    if (!shown.length) {
      ui.suList.replaceChildren(el("li", { class: "pick-empty" }, suDialogs.length ? t("noMatch") : t("suNoChats")));
      return;
    }
    ui.suList.replaceChildren(...shown.map((d) => el("li", {},
      el("label", { class: d.added ? "pick added" : "pick" },
        el("input", { type: "checkbox", value: d.id, checked: d.added || d.picked, disabled: d.added,
          onchange: (e) => { d.picked = e.target.checked; syncPickBar(); } }),
        el("span", { class: "pick-name" }, d.name),
        // a first copy cut short: tickable, says so, so its Retry survives a restart
        d.partial ? el("span", { class: "pick-kind bad" }, t("suPartial"))
          : el("span", { class: "pick-kind" }, d.added ? t("suAlready") : kindLabel(d.kind))))));
  }

  async function loadDialogs(opts) {
    ui.suList.replaceChildren(el("li", { class: "pick-empty" }, t("suLoading")));
    suDialogs = [];
    suShown = [];
    syncPickBar();
    api("/api/settings").then((s) => showMediaCap(s.max_media_mb)).catch(() => {});  // the Files choice names the limit
    // A copy running now (started before this page loaded, or before the setup was
    // closed): its rows and its Stop, not the chat list. The list comes back after it.
    if (!(opts && opts.keepNote) && await suCopyRunning()) return;
    if (!(opts && opts.keepNote)) ui.suProg.hidden = true;  // a fresh pick starts without old rows
    try {
      const r = await api("/api/setup/dialogs");
      suDialogs = r.dialogs || [];
      renderDialogs();
      if (r.switched) {  // listing showed another account: show only its chats now
        onAccountSwitch(r);
        const notes = [];
        if (r.parked) notes.push(r.comes_back ? t("suSwitched") : t("suParkedAt", r.parked));
        if (r.restored) notes.push(t("suRestored"));
        if (notes.length) ui.suWho.textContent = notes.join(" ");
      }
    } catch (e) {
      if (await suCopyRunning()) return;  // a copy began meanwhile: show it, with its Stop
      ui.suList.replaceChildren(el("li", { class: "pick-empty" },
        el("button", { class: "link-btn", type: "button", onclick: loadDialogs }, t("suRetry"))));
      suError(e);
    }
  }

  // Is a copy of ticked chats running? Then show its rows and its Stop and follow it.
  async function suCopyRunning() {
    let p;
    try { p = await api("/api/setup/add-progress"); } catch (e) { return false; }
    if (!p.running) return false;
    ui.suList.replaceChildren(el("li", { class: "pick-empty" }, t("suListLater")));
    ui.suChats.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    ui.suStop.disabled = false;
    suNote(t("suAdding"));
    renderProgress(p);
    suFollowAdd();
    return true;
  }

  ui.suCodes.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await suRun(ui.suCodes, () => api("/api/setup/codes", { api_id: ui.suApiId.value, api_hash: ui.suApiHash.value }));
    if (r) { ui.suApiHash.value = ""; suShow(r.logged_in ? "chats" : "phone"); }
  });
  ui.suPhone.addEventListener("submit", async (e) => {
    e.preventDefault();
    suAfterLogin(await suRun(ui.suPhone, () => api("/api/setup/phone", { phone: ui.suPhoneIn.value })));
  });
  ui.suCode.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await suRun(ui.suCode, () => api("/api/setup/code", { code: ui.suCodeIn.value }));
    ui.suCodeIn.value = "";
    suAfterLogin(r);
  });
  ui.suPassword.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await suRun(ui.suPassword, () => api("/api/setup/password", { password: ui.suPwIn.value }));
    ui.suPwIn.value = "";
    suAfterLogin(r);
  });
  // The first copy of the ticked chats runs on the server; one row per chat shows
  // waiting / copying N / done / a plain error with a Retry for that chat alone.
  function suLimit() {
    const limit = (document.querySelector('input[name="suLimit"]:checked') || {}).value || "1000";
    return limit === "all" ? "all" : Number(limit);
  }

  // The Files choice of this copy: small (default), none (text only) or all.
  function suFiles() {
    return (document.querySelector('input[name="suFiles"]:checked') || {}).value || "small";
  }

  // A copy cut short (partial) is a failure: its own words and a Retry, never "Done".
  function progBad(r) { return r.state === "error" || r.state === "stopped" || (r.state === "done" && !!r.partial); }

  // `running`: the copy goes on, so files still queued are downloading; once it has
  // ended, a file Stop skipped (or one left queued) waits for the next sync.
  function progText(r, running) {
    const later = (r.files_skipped || 0) + (running ? 0 : r.files_pending || 0);
    const files = (s) => (later > 0 ? `${s}. ${t("filesLater", later)}` : s);
    if (r.state === "copying") return t("progCopying", r.count || 0);
    if (r.state === "done" && r.partial) return files(t("progPartial", r.count || 0).replace(/\.$/, ""));
    if (r.state === "done" && running && r.files_pending > 0) return files(t("progDoneFiles", r.count || 0, r.files_pending));
    if (r.state === "done") return r.already ? t("progAlready") : files(t("progDone", r.count || 0));
    if (r.state === "stopped") return r.count ? files(t("progStoppedSaved", r.count).replace(/\.$/, "")) : t("progStopped");
    if (r.state === "error") return [r.message, r.hint].filter(Boolean).join(" ");
    return t("progWaiting");
  }

  function renderProgress(p) {
    const rows = p.rows || [];
    ui.suProg.hidden = !rows.length;
    const names = Object.fromEntries(suDialogs.map((d) => [d.id, d.name]));
    ui.suProgList.replaceChildren(...rows.map((r) => {
      const failed = progBad(r);
      return el("li", { class: `prog-row ${r.state}` },
        el("span", { class: "prog-name" }, r.name || names[r.id] || r.id),
        el("span", { class: failed ? "prog-state bad" : "prog-state" }, progText(r, p.running)),
        failed && !p.running ? el("button", { class: "link-btn", type: "button", onclick: () => suStartAdd([r.id], true) }, t("progRetry")) : null);
    }));
    // Stop is shown the whole time a copy runs, also on a page loaded mid-copy (suCopyRunning).
    suLast = p;
    ui.suStop.hidden = !p.running;
    ui.suStop.textContent = suHung(p) ? t("stopRetry") : t("stopBtn");
    if (p.stopping) ui.suStop.disabled = true;
  }

  let suLast = null;
  // A copy waiting on this window's own hung sync: Stop and retry frees that lock.
  function suHung(p) { return !!(p && p.running && p.lock && p.lock.stuck && p.lock.can_stop); }

  let suPolling = false;
  let suRetryAfterStop = false;
  async function suFollowAdd() {
    if (suPolling) return;
    suPolling = true;
    let p = null;
    let switchShown = false;
    try {
      for (;;) {
        p = await api("/api/setup/add-progress");
        renderProgress(p);
        if (p.switched && !switchShown) { switchShown = true; onAccountSwitch(); }
        if (!p.running) break;
        await new Promise((res) => setTimeout(res, 700));
      }
    } catch (err) {
      suError(err);
    } finally {
      suPolling = false;
      ui.suChats.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
    if (!p) return;
    if (suRetryAfterStop) {  // Stop and retry: copy again the chats the stop cut short
      suRetryAfterStop = false;
      const again = (p.rows || []).filter(progBad).map((r) => r.id);
      if (again.length) { suStartAdd(again, true); return; }
    }
    suAddedAny = suAddedAny || p.added > 0;
    if (p.error && !p.added) suNote([p.error.message, p.error.hint].filter(Boolean).join(" "), true);
    else if (p.failed) suNote(p.added ? t("suAddedSome", p.added, p.failed) : t("suNoneCopied", p.failed), true);
    else suNote(p.added ? t("suAdded", p.added) : t("suNoneAdded"), !p.added);
    refresh().catch(() => {});
    loadDialogs({ keepNote: true });
  }

  async function suStartAdd(ids, keep) {
    ui.suChats.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    ui.suStop.disabled = false;
    suNote(t("suAdding"));
    try {
      renderProgress(await api("/api/setup/add", { ids, limit: suLimit(), keep: !!keep, files: suFiles() }));
      ui.suProg.scrollIntoView({ block: "nearest" });
    } catch (err) {
      suError(err);
      ui.suChats.querySelectorAll("button").forEach((b) => { b.disabled = false; });
      return;
    }
    suFollowAdd();
  }

  ui.suChats.addEventListener("submit", (e) => {
    e.preventDefault();
    const ids = suDialogs.filter((d) => d.picked && !d.added).map((d) => d.id);
    if (!ids.length) { suNote(t("suPickOne"), true); return; }
    suStartAdd(ids, false);
  });
  ui.suStop.addEventListener("click", async () => {
    ui.suStop.disabled = true;
    suNote(t("stopping"));
    const hung = suHung(suLast);
    suRetryAfterStop = hung;  // only Stop and retry copies again; a plain Stop keeps what was saved
    try {
      await api(hung ? "/api/sync/stop" : "/api/stop", {});
    } catch (err) {
      suRetryAfterStop = false;
      suError(err);
    }
  });
  ui.suFolder.addEventListener("change", (e) => { if (e.target.name === "suHome") suFolderChoice(); });
  ui.suFolder.addEventListener("submit", async (e) => {
    e.preventDefault();
    const other = (document.querySelector('input[name="suHome"]:checked') || {}).value === "other";
    const r = await suRun(ui.suFolder, () => api("/api/setup/folder",
      other ? { choice: "other", path: ui.suFolderPathIn.value } : { choice: "keep" }));
    if (!r) return;
    suFolderDone = true;
    refresh().catch(() => {});
    suRoute(r);
  });
  ui.suFilter.addEventListener("input", renderDialogs);
  ui.suKinds.addEventListener("change", renderDialogs);
  ui.suPickAll.addEventListener("change", () => { TGPick.toggleAll(suShown); renderDialogs(); });
  ui.suSite.addEventListener("click", () => api("/api/setup/open-site", {}).catch(suError));
  ui.suChangeCodes.addEventListener("click", () => suShow("codes"));
  ui.suResend.addEventListener("click", async () => {
    await api("/api/setup/cancel", {}).catch(() => {});
    suShow("phone");
  });
  ui.suTerminal.addEventListener("click", async () => {
    await api("/api/setup/cancel", {}).catch(() => {});
    ui.setupDlg.close();
    openTerminalSetup();
  });
  ui.suDone.addEventListener("click", () => ui.setupDlg.close());
  ui.suClose.addEventListener("click", () => ui.setupDlg.close());
  ui.setupDlg.addEventListener("close", () => {
    // A login left half done would keep Get new messages waiting: end it.
    if (!ui.suCode.hidden || !ui.suPassword.hidden) api("/api/setup/cancel", {}).catch(() => {});
    refresh().catch(() => {});
    followRunning();  // a copy still running: the sync bar shows it with its Stop
    if (suAddedAny) ui.input.focus();
  });

  // ------------------------------------------------------------ settings

  let setTimer = null;
  function setNote(text) {
    ui.setStatus.textContent = text;
    clearTimeout(setTimer);
    if (text) setTimer = setTimeout(() => { ui.setStatus.textContent = ""; }, 5000);
  }

  function fillSelect(select, values, current, defaultLabel) {
    const opts = [["", defaultLabel], ...values.map((v) => [v, v.charAt(0).toUpperCase() + v.slice(1)])];
    if (current && !values.includes(current)) opts.push([current, current]);
    select.replaceChildren(...opts.map(([v, label]) => el("option", { value: v }, label)));
    select.value = current || "";
  }

  function showClaude(st) {
    const states = {
      connected: ["ok", t("stConnected", st.account, st.plan)],
      not_logged_in: ["warn", t("stNotLogged")],
      unknown: ["warn", t("stUnknown")],
      missing: ["bad", t("stMissing")],
    };
    const [dot, text] = states[st.state] || states.unknown;
    ui.claudeDot.className = `state-dot ${dot}`;
    ui.claudeStateText.textContent = st.version ? `${text} (v${st.version})` : text;
    const steps = st.state === "missing" ? t("fixMissing") : st.state === "not_logged_in" ? t("fixNotLogged") : [];
    ui.claudeSteps.replaceChildren(...steps.map((s) => el("li", {}, s)));
    ui.claudeSteps.hidden = !steps.length;
    ui.claudeInstall.hidden = st.state !== "missing";
    ui.claudeLogin.hidden = !(st.state === "not_logged_in" || st.state === "unknown" || st.state === "missing");
    ui.claudeLogin.disabled = st.state === "missing";
    ui.modelRow.hidden = !st.installed;
  }

  function showSettings(s) {
    fillSelect(ui.modelPick, s.models || [], s.model, t("modelDefault"));
    fillSelect(ui.effortPick, s.efforts || [], s.effort, t("effortDefault"));
    ui.effortField.hidden = !(s.efforts && s.efforts.length);
    ui.limitInput.value = s.usage_daily_limit ? String(s.usage_daily_limit) : "";
    ui.hardStop.checked = !!s.usage_hard_stop;
    ui.exportDir.value = s.export_dir || s.export_now || "";
    ui.exportDir.placeholder = s.export_default || "";
    ui.mediaCap.value = String(s.max_media_mb ?? 50);
    showMediaCap(s.max_media_mb);
    ui.mediaMode.value = s.media || "small";
    ui.dataPath.textContent = s.home || "";
    const acc = s.account || {};
    ui.addChatsBtn.hidden = !acc.logged_in;
    ui.accountLine.textContent = acc.logged_in ? t("loggedInAs", acc.name || "") : t("notLoggedIn");
    ui.logoutBtn.hidden = !acc.logged_in;
    ui.connectBtn.hidden = !!acc.logged_in;
    ui.logoutConfirm.hidden = true;
  }

  function showUsage(u) {
    const box = (label, x) => el("div", {}, el("dt", {}, label), el("dd", {}, t("tokensN", num(x.tokens)),
      el("span", { class: "sub" }, `${t("answersN", x.answers)}, ${t("usageBreak", num(x.input), num(x.output), num(x.cache_read + x.cache_write))}`)));
    ui.usageGrid.replaceChildren(box(t("today"), u.today), box(t("month"), u.month),
      ...(u.conversation ? [box(t("thisConv"), u.conversation)] : []));
    const cost = u.month && typeof u.month.cost_usd === "number" ? u.month.cost_usd : null;
    ui.usageCost.hidden = cost === null;
    if (cost !== null) ui.usageCost.textContent = t("costNote", cost.toFixed(2));
  }

  async function openSettings() {
    if (!ui.settings.open) ui.settings.showModal();
    setNote("");
    ui.claudeStateText.textContent = t("checking");
    try {
      const conv = app.conv ? `?conversation=${encodeURIComponent(app.conv.id)}` : "";
      const [s, u, st] = await Promise.all([api("/api/settings"), api(`/api/usage${conv}`), api("/api/claude-status")]);
      showSettings(s);
      showUsage(u);
      showClaude(st);
    } catch (e) {
      setNote(e.message);
    }
  }

  async function saveSetting(body) {
    try {
      showSettings(await api("/api/settings", body));
      setNote(t("saved"));
    } catch (e) {
      setNote(e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message);
    }
  }

  async function recheckClaude() {
    ui.claudeStateText.textContent = t("checking");
    ui.claudeDot.className = "state-dot";
    try {
      showClaude(await api("/api/claude-status?refresh=1"));
      showSettings(await api("/api/settings"));
      refresh().catch(() => {});
    } catch (e) {
      setNote(e.message);
    }
  }

  ui.settingsBtn.addEventListener("click", openSettings);
  ui.setClose.addEventListener("click", () => ui.settings.close());
  ui.settings.addEventListener("click", (e) => { if (e.target === ui.settings) ui.settings.close(); });
  ui.claudeCheck.addEventListener("click", recheckClaude);
  ui.claudeInstall.addEventListener("click", () => api("/api/claude/install", {}).catch((e) => setNote(e.message)));
  ui.claudeLogin.addEventListener("click", async () => {
    try {
      const r = await api("/api/claude/login", {});
      setNote(r.ok ? t("loginOpened") : (r.hint || t("errGeneric")));
    } catch (e) { setNote(e.message); }
  });
  ui.modelPick.addEventListener("change", () => saveSetting({ model: ui.modelPick.value }));
  ui.effortPick.addEventListener("change", () => saveSetting({ effort: ui.effortPick.value }));
  ui.limitInput.addEventListener("change", () => saveSetting({ usage_daily_limit: Number(ui.limitInput.value || 0) }));
  ui.hardStop.addEventListener("change", () => saveSetting({ usage_hard_stop: ui.hardStop.checked }));
  ui.exportDir.addEventListener("change", () => saveSetting({ export_dir: ui.exportDir.value.trim() }));
  ui.exportDefault.addEventListener("click", () => saveSetting({ export_dir: "" }));
  ui.openExport.addEventListener("click", () => api("/api/open-folder", {}).catch((e) => setNote(e.message)));
  ui.mediaCap.addEventListener("change", () => saveSetting({ max_media_mb: Number(ui.mediaCap.value || 50) }));
  ui.mediaMode.addEventListener("change", () => saveSetting({ media: ui.mediaMode.value }));
  ui.openData.addEventListener("click", () => api("/api/open-data", {}).catch((e) => setNote(e.message)));
  ui.openLogs.addEventListener("click", () => api("/api/open-logs", {}).catch((e) => setNote(e.message)));
  ui.copyDiag.addEventListener("click", async () => {
    try {
      const r = await api("/api/diagnostics");
      if (await copyText(r.text)) setNote(t("diagCopied"));
    } catch (e) {
      setNote(e.message);
    }
  });
  ui.addChatsBtn.addEventListener("click", () => openSetup("chats"));
  ui.logoutBtn.addEventListener("click", () => { ui.logoutConfirm.hidden = false; ui.logoutNo.focus(); });
  ui.logoutNo.addEventListener("click", () => { ui.logoutConfirm.hidden = true; ui.logoutBtn.focus(); });
  ui.connectBtn.addEventListener("click", () => { ui.settings.close(); openSetup(); });
  ui.logoutYes.addEventListener("click", async () => {
    const data = (document.querySelector('input[name="keep"]:checked') || {}).value || "keep";
    ui.logoutYes.disabled = true;
    try {
      const r = await api("/api/logout", { data });
      let note = t("loggedOut");
      if (r.backup) note += ` ${t("movedTo", r.backup)}`;
      if (r.telegram_note) note += ` ${r.telegram_note}`;
      setNote(note);
      showSettings(await api("/api/settings"));
      refresh().catch(() => {});
    } catch (e) {
      setNote(e.data && e.data.hint ? `${e.message} ${e.data.hint}` : e.message);
    } finally {
      ui.logoutYes.disabled = false;
    }
  });
  ui.toastClose.addEventListener("click", () => { ui.toast.hidden = true; });
  ui.toastShow.addEventListener("click", () => {
    if (toastFile) api("/api/reveal", { path: toastFile }).catch(() => {});
  });

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
    else if (e.key === "Escape") {
      document.querySelectorAll("details.export[open]").forEach((d) => { d.open = false; });
      if (ui.settings.open || ui.setupDlg.open) return;
      if (app.busy) stop(); else closeSide();
    }
  });
  ui.newChat.addEventListener("click", newConversation);
  // "What do I need to do today": the same question as typing it (offline: no tokens).
  ui.todayBtn.addEventListener("click", () => ask(t("todayAsk")));
  ui.todayEmpty.addEventListener("click", () => ask(t("todayAsk")));
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
  followRunning();  // a sync or a copy that runs while this page loads: its progress and its Stop
  refresh().then(() => {
    const start = params.get("c");
    if (start) openConversation(start);
    else newConversation();
    if (params.get("panel") === "settings") openSettings();
    if (params.get("panel") === "setup") openSetup(params.get("step") || undefined);
    // Who is this computer logged in to? Another account than the chats shown
    // switches to that account's chats now, not at the first sync (R15).
    api("/api/account/check", {}).then((r) => {
      if (!r.switched) return;
      onAccountSwitch();
      if (!app.syncing) ui.syncStatus.textContent = t("switchedTo", (r.me && r.me.name) || "");
    }).catch(() => {});
  }).catch((e) => {
    showEmpty(true);
    ui.syncStatus.textContent = e.message;
  });
})();
