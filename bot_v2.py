#!/usr/bin/env python3
"""
USDT 自动发货 Telegram Bot
钱包: THRgbNGBDt4NYg4mukGpx9xPHg4BDyQFum (TRC20)
Make Webhook: https://hook.eu2.make.com/2nslmaeqm2311uo4j5xoswbayp4e7c11
"""

import asyncio
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ============================================================
# ⚙️  配置 — 替换这3个值
# ============================================================
BOT_TOKEN        = "8511728121:AAEK9jLRP3XwEeKRR7ngqNmuExDdEua08Tg"       #
TRONGRID_API_KEY = "763b4057-8159-40d5-9d85-d11a17a92dc5"    # 
PRODUCT_FILE_URL = "https://drive.google.com/file/d/1BJUAaw9b7mTGGIh0qWX2Q--hSmKDaVJi/view?usp=drive_link"    # 产品文件链接

# 已固定配置（无需修改）
USDT_WALLET      = "THRgbNGBDt4NYg4mukGpx9xPHg4BDyQFum"
MAKE_WEBHOOK_URL = "https://hook.eu2.make.com/2nslmaeqm2311uo4j5xoswbayp4e7c11"
OWNER_CHAT_ID    = "5947001344"

PRODUCTS = {
    "p1": {
        "name": "AI 自动化工具包",
        "price": 47,
        "file_url": PRODUCT_FILE_URL,
        "desc": "包含完整 Make 自动化模板 + 操作指南"
    }
}

# ============================================================

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)
done_tx = set()


# ── 区块链核验 ──────────────────────────────────────────────

def hex_to_tron(h: str) -> str:
    import hashlib, base58
    if h.startswith("0x"): h = "41" + h[2:]
    b = bytes.fromhex(h)
    cs = hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4]
    return base58.b58encode(b + cs).decode()

async def verify(tx_hash: str, expected: float) -> dict:
    url = f"https://api.trongrid.io/v1/transactions/{tx_hash}"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        if not data.get("data"):
            return {"ok": False, "err": "❌ 交易不存在，请确认 Hash 是否正确"}
        tx = data["data"][0]
        if tx.get("ret", [{}])[0].get("contractRet") != "SUCCESS":
            return {"ok": False, "err": "❌ 交易未成功，请等待确认后重试"}
        contract = tx["raw_data"]["contract"][0]
        if contract["type"] != "TriggerSmartContract":
            return {"ok": False, "err": "❌ 不是 TRC20 交易"}
        val = contract["parameter"]["value"]
        raw = val.get("data", "")
        if len(raw) < 136:
            return {"ok": False, "err": "❌ 无法解析交易数据"}
        recipient = hex_to_tron("41" + raw[32:72])
        amount = int(raw[72:136], 16) / 1_000_000
        if recipient.lower() != USDT_WALLET.lower():
            return {"ok": False, "err": "❌ 收款地址不匹配"}
        if abs(amount - expected) > expected * 0.02:
            return {"ok": False, "err": f"❌ 金额不符：收到 {amount:.2f} USDT，期望 {expected} USDT"}
        return {"ok": True, "amount": amount}
    except asyncio.TimeoutError:
        return {"ok": False, "err": "⏱ 区块链 API 超时，请稍后重试"}
    except Exception as e:
        log.error(e)
        return {"ok": False, "err": "❌ 核验失败，请联系客服"}

async def call_make(chat_id, username, product_key, amount, tx_hash):
    p = PRODUCTS[product_key]
    payload = {
        "tg_chat_id": str(chat_id),
        "tg_username": username,
        "product": p["name"],
        "amount": str(amount),
        "tx_hash": tx_hash,
        "file_url": p["file_url"]
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(MAKE_WEBHOOK_URL, json=payload,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                return r.status == 200
    except Exception as e:
        log.error(f"Make error: {e}")
        return False


# ── Bot 处理 ────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(
        f"🛒 {p['name']} — ${p['price']} USDT",
        callback_data=f"buy_{k}"
    )] for k, p in PRODUCTS.items()]
    await update.message.reply_text(
        "👋 *欢迎！*\n\n支持 *USDT (TRC20)* 支付，请选择产品：",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def on_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.replace("buy_", "")
    if key not in PRODUCTS:
        await q.edit_message_text("❌ 产品不存在")
        return
    p = PRODUCTS[key]
    ctx.user_data["product"] = key
    await q.edit_message_text(
        f"🛒 *{p['name']}*\n{p['desc']}\n\n"
        f"💵 价格：*{p['price']} USDT (TRC20)*\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📤 转账至：\n`{USDT_WALLET}`\n\n"
        f"⚠️ 金额：*{p['price']} USDT*  网络：*TRC20*\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"✅ 转账完成后，把 *TxHash* 发给我",
        parse_mode="Markdown"
    )

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text):
        await on_txhash(update, ctx, text)
    elif ctx.user_data.get("product"):
        await update.message.reply_text("请发送 TxHash（64位字母数字）完成核验。")
    else:
        await update.message.reply_text("请发 /start 选择产品。")

async def on_txhash(update: Update, ctx: ContextTypes.DEFAULT_TYPE, tx: str):
    key = ctx.user_data.get("product")
    if not key:
        await update.message.reply_text("请先发 /start 选择产品。")
        return
    if tx in done_tx:
        await update.message.reply_text("⚠️ 该交易已处理过。")
        return
    p = PRODUCTS[key]
    msg = await update.message.reply_text("🔍 核验中，请稍候...")
    result = await verify(tx, p["price"])
    if not result["ok"]:
        await msg.edit_text(result["err"] + "\n\n如有疑问请联系客服。")
        return
    done_tx.add(tx)
    ctx.user_data.pop("product", None)
    await msg.edit_text("✅ 付款确认！正在发货...")
    u = update.effective_user
    ok = await call_make(u.id, u.username or u.first_name, key, result["amount"], tx)
    if not ok:
        await update.message.reply_text(
            f"✅ 付款已确认 ({result['amount']:.2f} USDT)\n\n"
            f"📦 你的产品：\n{p['file_url']}\n\n感谢购买！"
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_buy, pattern="^buy_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot 启动")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
