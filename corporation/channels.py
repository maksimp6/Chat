"""The company's own hands outside: Telegram and mail, behind the Court gate.

Owner, 08.11.2026: "i don't want they coming to me". The sales role publishes offers,
answers buyers, sends invoices and delivers products itself. Before anything leaves,
``gate`` runs the deterministic rules (code, not a prompt): the channel is enabled,
the text carries no secret, the recipient is someone who wrote to the company or the
company's own channel, a delivery has a confirmed payment. No rate limits and no
counts of ours (owner, 08.11.2026: "make them flexible do not make restrictions");
the platforms keep their own.
A refusal is a Court case and stops the channel; the record is written before the
send, and the send's result is written after it.

Keys are the owner's (core/.env on the phone), never in the ledger:
  LOGOS_CORP_TELEGRAM_TOKEN      the bot's token (BotFather)
  LOGOS_CORP_TELEGRAM_CHANNEL    the channel or chat id the offers are posted to
  LOGOS_CORP_TELEGRAM_PROVIDER   the payments provider token for invoices (optional)
  LOGOS_CORP_VK_TOKEN            a VK community token (wall, messages, docs)
  LOGOS_CORP_VK_GROUP            the community id (a number); its wall is the shelf, its messages the counter
  LOGOS_CORP_MAIL_HOST / _USER / _PASSWORD / _IMAP_HOST   the company mailbox (optional)
"""

from __future__ import annotations

import email
import email.message
import email.utils
import imaplib
import logging
import os
import re
import smtplib
import time
from pathlib import Path
from typing import Any

import requests

from corporation import ledger as journal

logger = logging.getLogger("logos.corporation.channels")

__all__ = ["CHANNEL_TOOLS", "GateRefused", "gate", "poll_inbox", "configured"]

_SECRET_PATTERNS = (
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),          # a Telegram bot token
    re.compile(r"\b(sk|ghp|gho|github_pat|xox[abp])[-_][A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\b(password|пароль|api[_ -]?key|token|токен)\s*[:=]\s*\S{6,}"),
    re.compile(r"\b\d{16,19}\b"),                             # a card number
)
_TEXT = {"type": "string", "minLength": 1, "maxLength": 4000}
_ID = {"type": "string", "minLength": 1, "maxLength": 64}


class GateRefused(ValueError):
    """An outward action the rules do not allow."""


def configured() -> dict[str, bool]:
    return {
        "telegram": bool(os.environ.get("LOGOS_CORP_TELEGRAM_TOKEN") and os.environ.get("LOGOS_CORP_TELEGRAM_CHANNEL")),
        "telegram_payments": bool(os.environ.get("LOGOS_CORP_TELEGRAM_PROVIDER")),
        "vk": bool(os.environ.get("LOGOS_CORP_VK_TOKEN") and os.environ.get("LOGOS_CORP_VK_GROUP")),
        "email": bool(os.environ.get("LOGOS_CORP_MAIL_HOST") and os.environ.get("LOGOS_CORP_MAIL_USER")),
    }


# ----- VK (a community: its wall is the shelf, its messages are the counter) ----------------

VK_API = "https://api.vk.com/method/"
VK_V = "5.199"


def _vk(method: str, **params: Any) -> Any:
    token = os.environ.get("LOGOS_CORP_VK_TOKEN") or ""
    response = requests.post(VK_API + method, data={**params, "access_token": token, "v": VK_V}, timeout=60)
    payload = response.json() if response.content else {}
    if "error" in payload:
        raise ValueError(f"vk {method}: {payload['error'].get('error_msg') or payload['error']}")
    return payload.get("response")


def _vk_group() -> int:
    return int(os.environ.get("LOGOS_CORP_VK_GROUP") or 0)


def _vk_peer(client: str) -> int:
    """The VK peer id behind a client id of the form vk<peer>."""
    return int(str(client)[2:]) if str(client).startswith("vk") else int(client)


def _vk_send(client: str, text: str, attachment: str | None = None) -> Any:
    params: dict[str, Any] = {"peer_id": _vk_peer(client), "random_id": int(time.time() * 1000) % 2_000_000_000, "message": text}
    if attachment:
        params["attachment"] = attachment
    return _vk("messages.send", **params)


def _vk_upload_doc(client: str, path: Path) -> str:
    """The product file as a VK document for a message: ``doc<owner>_<id>``."""
    server = _vk("docs.getMessagesUploadServer", type="doc", peer_id=_vk_peer(client))
    with path.open("rb") as stream:
        uploaded = requests.post(server["upload_url"], files={"file": (path.name, stream)}, timeout=120).json()
    saved = _vk("docs.save", file=uploaded["file"], title=path.name)
    doc = saved.get("doc") if isinstance(saved, dict) else None
    if not doc:
        raise ValueError("vk docs.save returned no document")
    return f"doc{doc['owner_id']}_{doc['id']}"


def poll_vk(st: journal.State) -> list[dict[str, Any]]:
    """Unread conversations of the community become client messages (client id vk<peer>)."""
    if not configured()["vk"] or not st.channels.get("vk", {}).get("enabled"):
        return []
    result = _vk("messages.getConversations", group_id=_vk_group(), filter="unread", count=20, extended=1)
    book = journal.ledger()
    new: list[dict[str, Any]] = []
    names = {p["id"]: " ".join(part for part in (p.get("first_name"), p.get("last_name")) if part) for p in (result or {}).get("profiles") or []}
    for item in (result or {}).get("items") or []:
        message = item.get("last_message") or {}
        peer = (item.get("conversation") or {}).get("peer", {}).get("id") or message.get("peer_id")
        text = message.get("text") or ""
        if not peer or not text:
            continue
        cid = f"vk{peer}"
        book.append("client.message", "channels", {"client": cid, "name": names.get(message.get("from_id")), "channel": "vk",
                                                     "text": text[:4000], "message_id": message.get("id")})
        _vk("messages.markAsRead", peer_id=peer, group_id=_vk_group())
        new.append({"client": cid, "text": text[:4000]})
    return new


def _actor() -> str:
    from corporation import cycle

    return cycle.current().get("role") or "owner"


def _refuse(channel: str, action: str, reason: str, **data: Any) -> GateRefused:
    book = journal.ledger()
    book.append("court.case", "court", {"channel": channel, "action": action, "reason": reason, **data})
    book.append("channel.stop", "court", {"name": channel, "reason": f"{action}: {reason}"})
    return GateRefused(f"the Court refused {action} on {channel}: {reason}; the channel is stopped until the owner's decision")


def gate(st: journal.State, channel: str, action: str, *, text: str = "", client: str | None = None,
         product: str | None = None) -> None:
    """The rules every outward action passes. Raises GateRefused (and files the case)."""
    if not st.channels.get(channel, {}).get("enabled"):
        raise GateRefused(f"channel {channel} is not enabled by the owner (корпорация: канал {channel} вкл)")
    if not configured().get(channel):
        raise GateRefused(f"channel {channel} has no keys in core/.env")
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text or ""):
            raise _refuse(channel, action, "the text carries something that looks like a secret or a card number")
    if client is not None and client not in st.clients:
        raise _refuse(channel, action, f"recipient {client} never wrote to the company", client=client)
    if action == "deliver":
        paid = st.clients.get(client or "", {}).get("paid") or []
        if product not in paid:
            raise GateRefused(f"client {client} has not paid for {product}; send an invoice first")


# ----- Telegram ----------------------------------------------------------------------------


def _tg(method: str, **params: Any) -> dict[str, Any]:
    token = os.environ.get("LOGOS_CORP_TELEGRAM_TOKEN") or ""
    files = params.pop("_files", None)
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=params, files=files, timeout=60)
    payload = response.json() if response.content else {}
    if not payload.get("ok"):
        raise ValueError(f"telegram {method}: {payload.get('description') or response.status_code}")
    return payload["result"]


def _channel_id() -> str:
    return os.environ.get("LOGOS_CORP_TELEGRAM_CHANNEL") or ""


def poll_inbox(st: journal.State | None = None) -> list[dict[str, Any]]:
    """New buyer messages and payments from every enabled channel, each written to the ledger."""
    st = st or journal.state()
    new = list(poll_vk(st))
    try:
        poll_mail(st)
    except Exception as exc:  # the mailbox is optional; a failure is logged, not fatal
        logger.warning("[CORP] mail inbox: %s", exc)
    if not configured()["telegram"] or not st.channels.get("telegram", {}).get("enabled"):
        return new
    cursor = st.channels.get("telegram", {}).get("cursor")
    params: dict[str, Any] = {"timeout": 0, "allowed_updates": '["message","pre_checkout_query"]'}
    if cursor:
        params["offset"] = int(cursor) + 1
    updates = _tg("getUpdates", **params)
    book = journal.ledger()
    new: list[dict[str, Any]] = []
    last = cursor
    for update in updates:
        last = update["update_id"]
        if update.get("pre_checkout_query"):
            query = update["pre_checkout_query"]
            _tg("answerPreCheckoutQuery", pre_checkout_query_id=query["id"], ok="true")
            continue
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        cid = str(chat.get("id") or "")
        if not cid or str(cid) == str(_channel_id()):
            continue
        if message.get("successful_payment"):
            payment = message["successful_payment"]
            amount = float(payment.get("total_amount") or 0) / 100.0
            payload = str(payment.get("invoice_payload") or "")
            product = payload.split(":")[1] if payload.startswith("offer:") and payload.count(":") >= 2 else None
            offer = payload.split(":")[0 + 1] if payload.startswith("offer:") else None
            book.append("revenue.confirmed", "cash",
                        {"amount": amount, "currency": payment.get("currency"), "client": cid, "channel": "telegram",
                         "offer": offer, "product": product or _product_of_offer(st, offer),
                         "payment_id": payment.get("telegram_payment_charge_id")})
            new.append({"client": cid, "paid": amount})
            continue
        text = message.get("text") or message.get("caption") or ""
        if not text:
            continue
        name = " ".join(part for part in (sender.get("first_name"), sender.get("last_name"), sender.get("username")) if part)
        book.append("client.message", "channels", {"client": cid, "name": name, "channel": "telegram", "text": text[:4000],
                                                     "message_id": message.get("message_id")})
        new.append({"client": cid, "name": name, "text": text[:4000]})
    if last is not None and str(last) != str(cursor):
        book.append("channel.cursor", "channels", {"name": "telegram", "cursor": last})
    return new


def _product_of_offer(st: journal.State, offer: str | None) -> str | None:
    item = st.offers.get(offer or "")
    return item["product"] if item else None


def _publish(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    oid = str(args["offer"])
    offer = st.offers.get(oid)
    if offer is None:
        raise ValueError(f"no such offer: {oid}")
    if offer["status"] == "published":
        return {"success": True, "offer": oid, "note": "already published", "message_id": offer.get("message_id")}
    channel = offer["channel"]
    how = {"telegram": f"напишите боту: /buy {oid}", "vk": f"напишите сообществу: куплю {oid}"}.get(channel, f"ответьте на это письмо: куплю {oid}")
    text = f"{offer['text']}\n\nЦена: {offer['price']:.0f} {offer['currency']}. Чтобы купить, {how}"
    gate(st, channel, "publish", text=text)
    book = journal.ledger()
    book.append("note", _actor(), {"text": f"publishing {oid} to {channel}"}, offer=oid, product=offer["product"], project=offer["project"])
    if channel == "telegram":
        sent = _tg("sendMessage", chat_id=_channel_id(), text=text)
        message_id = sent.get("message_id")
    elif channel == "vk":
        posted = _vk("wall.post", owner_id=-_vk_group(), from_group=1, message=text)
        message_id = (posted or {}).get("post_id")
    else:
        message_id = _mail_send(os.environ.get("LOGOS_CORP_MAIL_LIST") or os.environ.get("LOGOS_CORP_MAIL_USER") or "",
                                f"Оффер {oid}", text)
    book.append("offer.published", _actor(), {"channel": channel, "message_id": message_id, "text": text},
                offer=oid, product=offer["product"], project=offer["project"])
    return {"success": True, "offer": oid, "channel": channel, "message_id": message_id}


def _inbox(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    new = poll_inbox(st)
    st = journal.state()
    waiting = []
    for client in st.clients.values():
        messages = client["messages"]
        if messages and messages[-1]["from"] == "client":
            waiting.append({"client": client["id"], "name": client.get("name"), "last": messages[-1]["text"][:500],
                            "paid": client.get("paid"), "history": messages[-6:]})
    return {"success": True, "new": len(new), "waiting": waiting[:20], "offers": [o for o in st.offers.values() if o["status"] == "published"][-10:]}


def _reply(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    cid = str(args["client"])
    text = str(args["text"])[:4000]
    client = st.clients.get(cid)
    if client is None:
        raise ValueError(f"unknown client {cid}: only someone who wrote to the company can be answered")
    channel = client.get("channel") or "telegram"
    gate(st, channel, "reply", text=text, client=cid)
    book = journal.ledger()
    book.append("client.reply", _actor(), {"client": cid, "channel": channel, "text": text})
    if channel == "telegram":
        _tg("sendMessage", chat_id=cid, text=text)
    elif channel == "vk":
        _vk_send(cid, text)
    else:
        _mail_send(cid, "Ответ компании", text)
    return {"success": True, "client": cid, "channel": channel}


def _invoice(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    cid = str(args["client"])
    oid = str(args["offer"])
    offer = st.offers.get(oid)
    if offer is None:
        raise ValueError(f"no such offer: {oid}")
    if cid not in st.clients:
        raise ValueError(f"unknown client {cid}")
    channel = st.clients[cid].get("channel") or "telegram"
    gate(st, channel, "invoice", text=offer["text"], client=cid)
    provider = os.environ.get("LOGOS_CORP_TELEGRAM_PROVIDER")
    book = journal.ledger()
    if channel == "telegram" and provider:
        book.append("invoice", _actor(), {"client": cid, "channel": channel, "amount": offer["price"], "currency": offer["currency"]},
                    offer=oid, product=offer["product"])
        _tg("sendInvoice", chat_id=cid, title=offer["text"].split("\n")[0][:32] or oid, description=offer["text"][:255],
            payload=f"offer:{oid}:{offer['product']}", provider_token=provider, currency=offer["currency"],
            prices=f'[{{"label":"{oid}","amount":{int(offer["price"] * 100)}}}]')
        return {"success": True, "client": cid, "offer": oid, "invoice": "telegram"}
    # No payments provider: the price and the owner's payment instructions go as text.
    how = os.environ.get("LOGOS_CORP_PAYMENT_TEXT") or "Реквизиты для оплаты пришлёт компания отдельным сообщением."
    text = f"Счёт за {oid}: {offer['price']:.0f} {offer['currency']}. {how}"
    book.append("invoice", _actor(), {"client": cid, "channel": channel, "amount": offer["price"], "currency": offer["currency"], "text": text},
                offer=oid, product=offer["product"])
    if channel == "telegram":
        _tg("sendMessage", chat_id=cid, text=text)
    elif channel == "vk":
        _vk_send(cid, text)
    else:
        _mail_send(cid, f"Счёт {oid}", text)
    return {"success": True, "client": cid, "offer": oid, "invoice": "text",
            "note": "payment is confirmed by the owner (корпорация: продано) when no payments provider is configured"}


def _deliver(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    cid = str(args["client"])
    prid = str(args["product"])
    product = st.products.get(prid)
    if product is None:
        raise ValueError(f"no such product: {prid}")
    channel = st.clients.get(cid, {}).get("channel") or "telegram"
    gate(st, channel, "deliver", client=cid, product=prid)
    path = Path(product["path"])
    if not path.is_file():
        raise ValueError(f"the product file is gone: {path}")
    book = journal.ledger()
    book.append("delivery", _actor(), {"client": cid, "channel": channel, "path": str(path)}, product=prid, project=product["project"])
    if channel == "telegram":
        with path.open("rb") as stream:
            _tg("sendDocument", chat_id=cid, caption=product["title"][:1000], _files={"document": (path.name, stream)})
    elif channel == "vk":
        _vk_send(cid, product["title"][:1000], attachment=_vk_upload_doc(cid, path))
    else:
        _mail_send(cid, product["title"], product.get("summary") or product["title"], attachment=path)
    return {"success": True, "client": cid, "product": prid, "delivered": channel}


# ----- mail -------------------------------------------------------------------------------------


def _mail_send(to: str, subject: str, text: str, attachment: Path | None = None) -> str:
    host = os.environ.get("LOGOS_CORP_MAIL_HOST") or ""
    user = os.environ.get("LOGOS_CORP_MAIL_USER") or ""
    password = os.environ.get("LOGOS_CORP_MAIL_PASSWORD") or ""
    if not (host and user and to):
        raise ValueError("mail is not configured (LOGOS_CORP_MAIL_HOST/USER/PASSWORD)")
    message = email.message.EmailMessage()
    message["From"], message["To"], message["Subject"] = user, to, subject
    message.set_content(text)
    if attachment is not None:
        message.add_attachment(attachment.read_bytes(), maintype="application", subtype="octet-stream", filename=attachment.name)
    with smtplib.SMTP_SSL(host, int(os.environ.get("LOGOS_CORP_MAIL_PORT") or 465), timeout=60) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)
    return message["Message-ID"] or f"mail-{int(time.time())}"


def poll_mail(st: journal.State | None = None) -> int:
    """Unread mail in the company mailbox becomes client messages."""
    st = st or journal.state()
    host = os.environ.get("LOGOS_CORP_MAIL_IMAP_HOST") or os.environ.get("LOGOS_CORP_MAIL_HOST")
    user = os.environ.get("LOGOS_CORP_MAIL_USER") or ""
    password = os.environ.get("LOGOS_CORP_MAIL_PASSWORD") or ""
    if not (host and user and st.channels.get("email", {}).get("enabled")):
        return 0
    count = 0
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            return 0
        for num in data[0].split()[:50]:
            status, parts = imap.fetch(num, "(RFC822)")
            if status != "OK":
                continue
            parsed = email.message_from_bytes(parts[0][1])
            sender = email.utils.parseaddr(parsed.get("From", ""))[1]
            body = ""
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    break
            journal.ledger().append("client.message", "channels",
                                    {"client": sender, "name": parsed.get("From", ""), "channel": "email",
                                     "text": (parsed.get("Subject", "") + "\n" + body)[:4000]})
            count += 1
    return count


def _tool(fn, *, read_only: bool, schema: dict[str, Any], title: str, description: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": {"type": "object", "properties": schema["properties"], "required": schema.get("required", []),
                       "additionalProperties": False},
        "capabilities": ["corporation", "network"],
        "risk_level": "low" if read_only else "high",
        "read_only": read_only,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent"],
        "executor": {"type": "local"},
        "func": fn,
    }


CHANNEL_TOOLS = {
    "publish": _tool(_publish, read_only=False, schema={"properties": {"offer": _ID}, "required": ["offer"]},
                     title="Опубликовать оффер", description="Продажи: оффер уходит в канал компании (Telegram или почта), с ценой и способом покупки."),
    "inbox": _tool(_inbox, read_only=True, schema={"properties": {}, "required": []},
                   title="Входящие", description="Продажи: новые сообщения и оплаты покупателей; кто ждёт ответа."),
    "reply": _tool(_reply, read_only=False, schema={"properties": {"client": _ID, "text": _TEXT}, "required": ["client", "text"]},
                   title="Ответить покупателю", description="Продажи: ответ тому, кто написал компании (client из inbox)."),
    "invoice": _tool(_invoice, read_only=False, schema={"properties": {"client": _ID, "offer": _ID}, "required": ["client", "offer"]},
                     title="Выставить счёт", description="Продажи: счёт покупателю по офферу (Telegram Payments, если настроен провайдер; иначе текстом)."),
    "deliver": _tool(_deliver, read_only=False, schema={"properties": {"client": _ID, "product": _ID}, "required": ["client", "product"]},
                     title="Отдать продукт", description="Продажи: файл продукта покупателю; только после подтверждённой оплаты."),
}
