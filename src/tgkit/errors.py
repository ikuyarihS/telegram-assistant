"""Errors a non-technical user can act on, in the language of the Telegram Assistant window.

Every message is written in English in the code. The app's server shows it in
Vietnamese when the window is in Vietnamese: localize() looks the text up in
VI (exact texts) or VI_PATTERNS (texts with a name or a number inside). A text
with no entry stays in English, never half translated.
"""

import re


class UserError(Exception):
    """A problem the user can fix. `hint` is the next thing to do."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        text = self.message
        if self.hint:
            text += "\n" + self.hint
        return text


VI = {
    # setup in the window (setupflow) and Telegram's answers (cli._FRIENDLY)
    "That api_id does not look right.": "api_id này có vẻ chưa đúng.",
    "It is a number, like 1234567.": "Đó là một dãy số, ví dụ 1234567.",
    "That api_hash does not look right.": "api_hash này có vẻ chưa đúng.",
    "It has 32 letters and digits. Copy it from the page and paste it here.":
        "Mã này có 32 chữ cái và chữ số. Chép nó từ trang web rồi dán vào đây.",
    "That phone number looks too short.": "Số điện thoại này có vẻ quá ngắn.",
    "Type it with the country code, like +84 912 345 678.": "Gõ kèm mã quốc gia, ví dụ +84 912 345 678.",
    "The login code is no longer waited for.": "Mã đăng nhập này không còn được chờ nữa.",
    "Press Send code again.": "Bấm Gửi mã lần nữa.",
    "Type the code Telegram sent you.": "Gõ mã Telegram đã gửi cho bạn.",
    "Look in your Telegram app.": "Xem trong ứng dụng Telegram của bạn.",
    "The password is no longer waited for.": "Mật khẩu không còn được chờ nữa.",
    "Start again with Send code.": "Làm lại từ bước Gửi mã.",
    "Type your two-step password.": "Gõ mật khẩu hai bước của bạn.",
    "It is the extra password you set in Telegram.": "Đó là mật khẩu phụ bạn đã đặt trong Telegram.",
    "Pick at least one chat.": "Chọn ít nhất một nhóm.",
    "Tick the chats Telegram Assistant should follow.": "Đánh dấu các nhóm Telegram Assistant cần theo dõi.",
    "Could not reach Telegram.": "Không kết nối được với Telegram.",
    "Telegram Assistant is still busy with Telegram (getting new messages or copying chats).":
        "Telegram Assistant vẫn đang bận với Telegram (đang lấy tin mới hoặc sao chép nhóm).",
    "Wait a moment and try again.": "Chờ một lát rồi thử lại.",
    "Something went wrong while talking to Telegram.": "Có lỗi khi làm việc với Telegram.",
    "Something went wrong in Telegram Assistant.": "Telegram Assistant gặp lỗi.",
    # 0.2.4: copying ticked chats, a hung sync, account switch
    "Telegram Assistant is still copying the chats you ticked.": "Telegram Assistant vẫn đang sao chép các nhóm bạn đã chọn.",
    "Wait until it is done, or press Stop and retry.": "Chờ xong, hoặc bấm Dừng và thử lại.",
    "Wait until it is done, or press Stop.": "Chờ xong, hoặc bấm Dừng.",
    "Wait until it is done, then press Get new messages again.": "Chờ xong rồi bấm Lấy tin mới lần nữa.",
    "Pick one of the Files choices.": "Chọn một mục trong phần Tệp.",
    "Text and small files is the usual one.": "Thường chọn Tin nhắn và tệp nhỏ.",
    "Stopped before this chat was copied.": "Đã dừng trước khi sao chép nhóm này.",
    "Press Retry for this chat to copy the rest.": "Bấm Thử lại cho nhóm này để sao chép phần còn lại.",
    "Telegram could not be reached.": "Không kết nối được Telegram.",
    "Finish it or close the setup, then add chats.": "Làm xong hoặc đóng phần cài đặt, rồi thêm nhóm.",
    "Telegram did not list this chat.": "Telegram không còn liệt kê nhóm này.",
    "Open Add chats again to see your chats as they are now.":
        "Mở Thêm nhóm lại để xem danh sách nhóm hiện tại.",
    "This chat was not copied.": "Nhóm này chưa được sao chép.",
    "Press Retry for this chat.": "Bấm Thử lại cho nhóm này.",
    "Stopped before this chat was copied.": "Đã dừng trước khi sao chép nhóm này.",
    "Check your internet connection, then press Retry for this chat.":
        "Kiểm tra kết nối mạng, rồi bấm Thử lại cho nhóm này.",
    "Another Telegram Assistant window or terminal is using this data folder.":
        "Một cửa sổ Telegram Assistant hoặc cửa sổ lệnh khác đang dùng thư mục dữ liệu này.",
    "Close it, then press Get new messages again. Telegram Assistant never stops another program.":
        "Đóng nó, rồi bấm Lấy tin mới lần nữa. Telegram Assistant không bao giờ dừng chương trình khác.",
    "Telegram Assistant could not stop the hung task.": "Telegram Assistant chưa dừng được việc đang bị treo.",
    "Close Telegram Assistant and open it again: that always frees it. Nothing was deleted.":
        "Đóng Telegram Assistant rồi mở lại: việc đó luôn giải phóng nó. Không có gì bị xóa.",
    "Telegram Assistant could not read or write a file in its data folder.":
        "Telegram Assistant không đọc hoặc ghi được một tệp trong thư mục dữ liệu.",
    "Close any program that has a file of Telegram Assistant open, then try again.":
        "Đóng chương trình đang mở tệp của Telegram Assistant, rồi thử lại.",
    "Try again in a moment. If it keeps happening, press Copy diagnostics in Settings "
    "and send it to whoever gave you Telegram Assistant.":
        "Thử lại sau giây lát. Nếu vẫn lỗi, bấm Chép thông tin chẩn đoán trong Cài đặt "
        "rồi gửi cho người đã đưa bạn Telegram Assistant.",
    "Telegram Assistant is still getting new messages.": "Telegram Assistant vẫn đang lấy tin mới.",
    "Wait until it is done, then open Add chats again.": "Chờ xong rồi mở Thêm nhóm lại.",
    "Wait until it is done, then press Add again.": "Chờ xong rồi bấm Thêm lần nữa.",
    "A Telegram login is under way in the setup window.": "Đang đăng nhập Telegram trong cửa sổ cài đặt.",
    "Finish it or close the setup, then press Get new messages again.":
        "Hoàn tất hoặc đóng cửa sổ cài đặt, rồi bấm Lấy tin mới lần nữa.",
    "This computer is now logged in to another Telegram account.":
        "Máy này đang đăng nhập một tài khoản Telegram khác.",
    "Open Settings and press Add chats once: Telegram Assistant then shows that account's chats.":
        "Mở Cài đặt và bấm Thêm nhóm một lần: Telegram Assistant sẽ hiện các nhóm của tài khoản đó.",
    "Move it away, then try again.": "Chuyển nó đi chỗ khác rồi thử lại.",
    "Check your internet connection and try again.": "Kiểm tra kết nối mạng rồi thử lại.",
    "Try again in a moment.": "Thử lại sau giây lát.",
    "Check the code in your Telegram app and type it again.": "Xem lại mã trong ứng dụng Telegram rồi gõ lại.",
    "Press Send code again to get a new one.": "Bấm Gửi mã lần nữa để nhận mã mới.",
    "Type your two-step password again.": "Gõ lại mật khẩu hai bước.",
    "Copy api_id and api_hash again from my.telegram.org.": "Chép lại api_id và api_hash từ my.telegram.org.",
    "Telegram asked Telegram Assistant to slow down.": "Telegram yêu cầu Telegram Assistant chậm lại.",
    "Please wait a few minutes and try again.": "Chờ vài phút rồi thử lại.",
    "Your Telegram login on this computer has ended.": "Phiên đăng nhập Telegram trên máy này đã hết.",
    "Run: tgkit setup": "Bấm Kết nối Telegram (hoặc chạy: tgkit setup).",
    "Your Telegram login on this computer was closed.": "Phiên đăng nhập Telegram trên máy này đã bị đóng.",
    "Your Telegram login file was used on two computers at once.":
        "Tệp đăng nhập Telegram đã được dùng trên hai máy cùng lúc.",
    "Telegram did not accept your api_id / api_hash.": "Telegram không chấp nhận api_id / api_hash của bạn.",
    "Run: tgkit setup and type them again.": "Bấm Kết nối Telegram (hoặc chạy: tgkit setup) rồi gõ lại.",
    "Telegram did not accept that phone number.": "Telegram không chấp nhận số điện thoại này.",
    "Run tgkit setup again and type it with the country code, like +84 912 345 678.":
        "Chạy lại tgkit setup và gõ kèm mã quốc gia, ví dụ +84 912 345 678.",
    "That login code was not right.": "Mã đăng nhập này chưa đúng.",
    "Run tgkit setup again.": "Chạy lại tgkit setup.",
    "That login code has expired.": "Mã đăng nhập này đã hết hạn.",
    "Run tgkit setup again to get a new one.": "Chạy lại tgkit setup để nhận mã mới.",
    "That two-step password was not right.": "Mật khẩu hai bước chưa đúng.",
    # the data folder step of the setup
    "The data folder is set by TGKIT_HOME, so it cannot be changed here.":
        "Thư mục dữ liệu được đặt bằng TGKIT_HOME nên không đổi được ở đây.",
    "Keep it, or ask the person who set up this computer.": "Giữ nguyên, hoặc hỏi người đã cài máy này.",
    "That is a file, not a folder.": "Đó là một tệp, không phải thư mục.",
    "Pick a folder.": "Chọn một thư mục.",
    "Pick another folder.": "Chọn thư mục khác.",
    "For example: D:\\Telegram Assistant": "Ví dụ: D:\\Telegram Assistant",
    "A login is under way.": "Đang đăng nhập dở.",
    "Finish it or close the setup, then change the folder.": "Đăng nhập xong hoặc đóng phần cài đặt, rồi đổi thư mục.",
    "Wait until it is done, then change the folder.": "Chờ xong rồi đổi thư mục.",
    # config
    "Telegram Assistant is not connected to Telegram yet.": "Telegram Assistant chưa kết nối với Telegram.",
    "Run: tgkit setup   (it walks you through it, about 3 minutes).":
        "Bấm Kết nối Telegram: khoảng 3 phút, từng bước một.",
    "The saved Telegram api_id / api_hash look wrong.": "api_id / api_hash đã lưu có vẻ sai.",
    "Run: tgkit setup   and type them again.": "Bấm Kết nối Telegram rồi gõ lại.",
    "The settings file is busy: another Telegram Assistant is changing it.":
        "Tệp cài đặt đang bận: một Telegram Assistant khác đang sửa nó.",
    # the app server
    "The daily limit must be a whole number of tokens.": "Giới hạn mỗi ngày phải là một số token nguyên.",
    "Use 0 for no limit.": "Dùng 0 nếu không giới hạn.",
    "The daily limit cannot be below 0.": "Giới hạn mỗi ngày không được nhỏ hơn 0.",
    "Type the full path of the folder.": "Gõ đường dẫn đầy đủ của thư mục.",
    "For example: D:\\Reports\\Telegram Assistant": "Ví dụ: D:\\Reports\\Telegram Assistant",
    "The file size limit must be a whole number of MB.": "Giới hạn kích thước tệp phải là một số MB nguyên.",
    "For example: 50": "Ví dụ: 50",
    "The file size limit must be between 1 and 4000 MB.": "Giới hạn kích thước tệp phải từ 1 đến 4000 MB.",
    "Pick one from the list.": "Chọn một mục trong danh sách.",
    "New messages are being fetched right now.": "Telegram Assistant đang lấy tin mới.",
    "Wait until it is done, then log out.": "Chờ xong rồi đăng xuất.",
    "Wait until it is done, then add chats.": "Chờ xong rồi thêm nhóm.",
    "Wait until it is done, then log in.": "Chờ xong rồi đăng nhập.",
    "That file is not in your export folder any more.": "Tệp này không còn trong thư mục lưu tệp.",
    "Open the folder from Settings.": "Mở thư mục từ Cài đặt.",
    "That is not a link to a chat in Telegram Assistant.": "Đây không phải liên kết tới một nhóm trong Telegram Assistant.",
    "Open it from a result in this window.": "Mở nó từ một kết quả trong cửa sổ này.",
    "Telegram Desktop cannot open this kind of link.": "Telegram Desktop không mở được loại liên kết này.",
    "Open Telegram and find the chat by its name.": "Mở Telegram và tìm cuộc trò chuyện theo tên.",
    "Telegram Desktop is not on this computer.": "Máy này chưa có Telegram Desktop.",
    "Install it from desktop.telegram.org, then press the link again.":
        "Cài nó từ desktop.telegram.org rồi bấm lại liên kết.",
    "Type a question first.": "Hãy gõ câu hỏi trước.",
    "A sync is already running.": "Đang có một lượt lấy tin khác.",
    "Not allowed.": "Không được phép.",
    "That request was not understood.": "Không hiểu yêu cầu này.",
    "Not found.": "Không tìm thấy.",
    "Claude stopped without an answer.": "Claude dừng lại mà không trả lời.",
    # account
    "Close every Telegram Assistant window and terminal, then log out again.":
        "Đóng mọi cửa sổ Telegram Assistant và terminal rồi đăng xuất lại.",
    "Choose keep or backup.": "Chọn giữ lại hoặc sao lưu.",
    # engine, conversations, tasks
    "Use open, done or dropped.": "Dùng open, done hoặc dropped.",
    "That chat is not in Telegram Assistant.": "Nhóm này không có trong Telegram Assistant.",
    "Pick a message from the results again.": "Chọn lại một tin trong kết quả.",
    "That conversation does not exist.": "Cuộc trò chuyện này không tồn tại.",
    "Pick one from the list on the left.": "Chọn một cuộc trò chuyện trong danh sách bên trái.",
    "Use the number shown in: tgkit tasks": "Dùng số hiện trong danh sách việc.",
    "See the numbers with: tgkit tasks all": "Xem các số trong danh sách Tất cả việc.",
    # store
    "Another Telegram Assistant sync is already running for this data folder.":
        "Đang có một lượt lấy tin Telegram Assistant khác cho thư mục dữ liệu này.",
    "Wait for it to finish, then try again.": "Chờ xong rồi thử lại.",
    "The task list is busy: another Telegram Assistant is changing it.": "Danh sách việc đang bận: một Telegram Assistant khác đang sửa nó.",
    "You have no chats in Telegram Assistant yet.": "Telegram Assistant chưa có nhóm nào.",
    "Run: tgkit setup   (or: tgkit add) to pick the chats to follow.":
        "Bấm Kết nối Telegram (hoặc Thêm nhóm) để chọn các nhóm cần theo dõi.",
    "Type part of one of these names.": "Gõ một phần tên của một trong các nhóm này.",
    "Use something like 7d (7 days), 24h (24 hours), 2w (2 weeks), today, all, or a date like 2026-09-01.":
        "Dùng dạng như 7d (7 ngày), 24h (24 giờ), 2w (2 tuần), today, all, hoặc một ngày như 2026-09-01.",
    # net
    "The Telegram part of Telegram Assistant is missing on this computer.": "Máy này thiếu phần Telegram của Telegram Assistant.",
    "Run the installer again (see README).": "Chạy lại bộ cài (xem README).",
    "You are not logged in to Telegram on this computer yet.": "Máy này chưa đăng nhập Telegram.",
    "To get its new messages run: tgkit sync": "Bấm Lấy tin mới để lấy tin mới của nhóm này.",
    "Check that the chat still exists and that you are a member, then try again.":
        "Kiểm tra nhóm vẫn còn và bạn vẫn là thành viên, rồi thử lại.",
    "Check the spelling, or run tgkit add to pick from a list.": "Kiểm tra chính tả, hoặc chọn trong danh sách nhóm.",
    "Repair or remove that line, then run tgkit sync again.": "Sửa hoặc bỏ dòng đó, rồi lấy tin mới lại.",
    # export
    "What should be searched?": "Cần tìm gì?",
    'For example: tgkit export search "biên bản"': 'Ví dụ: tgkit export search "biên bản"',
    "This result cannot be saved as a file.": "Kết quả này không lưu thành tệp được.",
    "Save the whole conversation instead.": "Hãy lưu cả cuộc trò chuyện.",
    "Export a conversation, a search, a summary or the tasks.":
        "Lưu một cuộc trò chuyện, một lần tìm kiếm, một bản tóm tắt hoặc danh sách việc.",
    "Move some of them away and try again.": "Chuyển bớt vài tệp đi rồi thử lại.",
    "Use a full path, like C:\\Users\\you\\Documents\\Telegram Assistant":
        "Dùng đường dẫn đầy đủ, ví dụ C:\\Users\\you\\Documents\\Telegram Assistant",
    "Choose docx (Word), xlsx (Excel), csv or md.": "Chọn docx (Word), xlsx (Excel), csv hoặc md.",
    "Check the export folder in Settings, or choose another one.":
        "Kiểm tra thư mục lưu tệp trong Cài đặt, hoặc chọn thư mục khác.",
    "Pick the file from the results again.": "Chọn lại tệp trong kết quả.",
    "That message was not found.": "Không tìm thấy tin nhắn này.",
    "That message was not found in the local copy.": "Không tìm thấy tin nhắn này trong bản sao trên máy.",
    "Telegram Assistant only copies files it got during sync (big files are skipped). "
    "Open the message in Telegram to download it.":
        "Telegram Assistant chỉ chép các tệp nhận được khi lấy tin (tệp lớn bị bỏ qua). Mở tin nhắn trên Telegram để tải về.",
}

# Texts with a name, a number or a path inside: (pattern, Vietnamese with {0}, {1}...).
# The parts caught are shown as they are (a chat name stays a chat name).
VI_PATTERNS = [(re.compile(p, re.S), vi) for p, vi in (
    (r"The login file (.+) is in use and could not be removed\.", "Tệp đăng nhập {0} đang được dùng nên chưa xóa được."),
    (r"'(.*)' is not a choice for your chats\.", "'{0}' không phải lựa chọn cho các nhóm của bạn."),
    (r"'(.*)' is not a task state\.", "'{0}' không phải trạng thái của việc."),
    (r"'(.*)' is not a model Telegram Assistant can choose\.", "'{0}' không phải mô hình Telegram Assistant chọn được."),
    (r"This Claude Code has no effort level '(.*)'\.", "Claude Code này không có mức suy nghĩ '{0}'."),
    (r"The task list (.+) is not in the expected shape\. Nothing was changed\.",
     "Danh sách việc {0} không đúng dạng. Chưa thay đổi gì."),
    (r"'(.*)' is not a task number\.", "'{0}' không phải số thứ tự của việc."),
    (r"There is no task number (.+)\.", "Không có việc số {0}."),
    (r"'(.*)' is already in Telegram Assistant\.", "'{0}' đã có trong Telegram Assistant."),
    (r"Could not read any messages from '(.*)'\.", "Không đọc được tin nào từ '{0}'."),
    (r"Nothing was searched: this computer is now logged in to (.+), but the chats in Telegram Assistant belong to (.+)\.",
     "Chưa tìm gì cả: máy này đang đăng nhập {0}, nhưng các nhóm trong Telegram Assistant là của {1}."),
    (r"To use (.+)'s chats, press Get new messages in the Telegram Assistant window \(or run: tgkit sync\); "
     r"Telegram Assistant then switches to them and keeps the others aside\. Then search again\.",
     "Để dùng các nhóm của {0}, bấm Lấy tin mới trong cửa sổ Telegram Assistant (hoặc chạy: tgkit sync); "
     "Telegram Assistant sẽ chuyển sang các nhóm đó và cất các nhóm kia sang một bên. Rồi tìm lại."),
    (r"'(.*)' appeared in Telegram Assistant while adding it; nothing was overwritten\.",
     "'{0}' đã xuất hiện trong Telegram Assistant trong lúc thêm; không ghi đè gì."),
    (r"The fetched copy was left in (.+)\.", "Bản đã tải được để lại ở {0}."),
    (r"Telegram has no chat '(.*)' that you can read\.", "Telegram không có nhóm '{0}' mà bạn đọc được."),
    (r"(.+)/messages\.jsonl line (\S+) is damaged \((.*)\)\. Refusing to sync this chat: "
     r"sync would duplicate messages on top of it\.",
     "Dòng {1} của {0}/messages.jsonl bị hỏng ({2}). Không lấy tin cho nhóm này: làm vậy sẽ chép trùng tin."),
    (r"The file (.+) could not be read: it is damaged or in use\. Nothing was changed\.",
     "Không đọc được tệp {0}: tệp bị hỏng hoặc đang được dùng. Chưa thay đổi gì."),
    (r"Stopped: (\d+) messages of '(.*)' are saved; older messages were not copied yet\.",
     "Đã dừng: đã lưu {0} tin nhắn của '{1}'; các tin cũ hơn chưa được sao chép."),
    (r"Rename or fix (.+) and try again\.", "Đổi tên hoặc sửa {0} rồi thử lại."),
    (r"I did not understand the time '(.*)'\.", "Không hiểu khoảng thời gian '{0}'."),
    (r"No chat matches '(.*)'\. Your chats are:\n(.*)", "Không có nhóm nào khớp với '{0}'. Các nhóm của bạn:\n{1}"),
    (r"Telegram Assistant cannot export '(.*)'\.", "Telegram Assistant không lưu được '{0}'."),
    (r"Too many files with this name in (.+)\.", "Đã có quá nhiều tệp cùng tên trong {0}."),
    (r"'(.*)' is not a full folder path\.", "'{0}' không phải đường dẫn thư mục đầy đủ."),
    (r"Telegram Assistant cannot save \.(\w+) files\.", "Telegram Assistant không lưu được tệp .{0}."),
    (r"The file could not be saved \((.*)\)\.", "Không lưu được tệp ({0})."),
    (r"The file could not be copied \((.*)\)\.", "Không chép được tệp ({0})."),
    (r"That folder could not be made \((.*)\)\.", "Không tạo được thư mục này ({0})."),
    (r"Telegram said: (.+)\.", "Telegram báo: {0}."),
    (r"Telegram Assistant could not switch accounts: (.+) is already there\.",
     "Telegram Assistant chưa chuyển tài khoản được: {0} đã có sẵn."),
    (r"Could not start the sync \((.*)\)\.", "Không bắt đầu lấy tin được ({0})."),
    (r"Could not copy '(.*)'\.", "Không sao chép được '{0}'."),
    (r"Telegram Assistant could not move (.+): the file is in use or the disk is full\. Nothing was deleted\.",
     "Telegram Assistant chưa chuyển được {0}: tệp đang được dùng hoặc ổ đĩa đã đầy. Không có gì bị xóa."),
    (r"Today's Claude limit that you set is reached \((.+) of (.+) tokens\)\. Answered without Claude\.",
     "Đã đạt giới hạn Claude hôm nay bạn đặt ({0} trên {1} token). Đã trả lời không dùng Claude."),
    (r"You are over the daily Claude limit you set \((.+) of (.+) tokens\)\. Claude still answers; "
     r"change it in Settings\.",
     "Bạn đã dùng quá giới hạn Claude mỗi ngày bạn đặt ({0} trên {1} token). Claude vẫn trả lời; "
     "đổi mức này trong Cài đặt."),
    (r"(.+) Answered without Claude\.", "{0} Đã trả lời không dùng Claude."),
    (r"(.+) is not on this computer\.", "{0} không có trên máy này."),
)]


def is_vi(lang: str | None) -> bool:
    return str(lang or "").lower().startswith("vi")


def localize(text: str | None, lang: str | None) -> str | None:
    """`text` in the window's language: Vietnamese when lang is vi and Telegram Assistant has it."""
    if not text or not is_vi(lang):
        return text
    if text in VI:
        return VI[text]
    for pattern, vi in VI_PATTERNS:
        m = pattern.fullmatch(text)
        if m:
            return vi.format(*(VI.get(g, g) for g in m.groups()))
    return text
