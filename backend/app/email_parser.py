"""Parses uploaded .txt and .msg files into a normalised raw-email dict:
{sender, recipients, subject, sent_date, body, attachments[]}

.msg  — Outlook OLE compound file, parsed with extract-msg.
.txt  — plain text; header lines (From:/To:/Subject:/Date:) are honoured
        if present, otherwise the whole file is treated as the body.
"""
import re
import tempfile


def parse_txt(content: bytes) -> dict:
    text = content.decode("utf-8", errors="replace")
    headers = {"from": None, "to": None, "subject": None, "date": None}
    body_lines, in_headers = [], True
    for line in text.splitlines():
        if in_headers:
            m = re.match(r"^(From|To|Cc|Subject|Date|Sent)\s*:\s*(.*)$", line.strip(), re.IGNORECASE)
            if m:
                key = m.group(1).lower()
                if key == "sent":
                    key = "date"
                if key in ("from", "to", "subject", "date") and headers.get(key) is None:
                    headers[key] = m.group(2).strip()
                continue
            if line.strip() == "" and any(headers.values()):
                in_headers = False
                continue
            in_headers = False
        body_lines.append(line)
    return {
        "sender": headers["from"],
        "recipients": headers["to"],
        "subject": headers["subject"],
        "sent_date": headers["date"],
        "body": "\n".join(body_lines).strip() or text.strip(),
        "attachments": [],
    }


def parse_msg(content: bytes) -> dict:
    import extract_msg  # lazy import — only needed for .msg uploads

    with tempfile.NamedTemporaryFile(suffix=".msg", delete=True) as tmp:
        tmp.write(content)
        tmp.flush()
        msg = extract_msg.openMsg(tmp.name)
        try:
            return {
                "sender": msg.sender,
                "recipients": msg.to,
                "subject": msg.subject,
                "sent_date": str(msg.date) if msg.date else None,
                "body": (msg.body or "").strip(),
                "attachments": [getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or "attachment"
                                for a in (msg.attachments or [])],
            }
        finally:
            msg.close()


def parse_email_file(filename: str, content: bytes) -> tuple[str, dict]:
    """Returns (file_type, raw_email). Raises ValueError on unsupported type."""
    lower = filename.lower()
    if lower.endswith(".msg"):
        return "msg", parse_msg(content)
    if lower.endswith(".txt"):
        return "txt", parse_txt(content)
    raise ValueError("Unsupported file type — please upload a .txt or .msg email file.")
