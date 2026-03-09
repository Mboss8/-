#!/usr/bin/env python3
"""
USDT 支付自动发货 Telegram Bot
链: TRC20 (TRON)
功能: 接收TxHash → 区块链核验 → Make Webhook → 自动发货
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
# ⚙️  配置区 - 填入你自己的信息
# ============================================================

BOT_TOKEN = "8529907934:AAHYdu9azza5t47_BnvUCJFP-PMT5PeX864"           # 你的 Bot Token

# USDT 收款钱包地址 (TRC20)
USDT_WALLET = "THRgbNGBDt4NYg4mukGpx9xPHg4BDyQFum" # 例: TXxx...

# 产品配置
PRODUCTS = {
    "product_1": {
        "name": "AI 自动化工具包",
        "price_usdt": 47,              # 价格 (USDT)
        "file_url": "YOUR_FILE_URL",   # 文件链接 或 Telegram file_id
        "description": "包含完整 Make 自动化模板 + 操作指南"
    }
    # 可以添加更多产品:
    # "product_2": { "name": "...", "price_usdt": 97, ... }
}

# Make Webhook URL (在 Make 界面 Webhook 模块里复制)
MAKE_WEBHOOK_URL = "Https://hook.eu2.make.com/2nslmaeqm2311uo4j5xoswbayp4e7c11"

# 区块链 API (Trongrid)
TRONGRID_API_KEY = "01f5829f-6fc2-4fec-a852-e34e3d0ddfe6"  # 重新生成后填这里
TRONGRID_BASE = "https://api.trongrid.io"

# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 记录已处理的 TxHash，防止重复发货
processed_tx = set()


# ── 区块链核验函数 ──────────────────────────────────────────

async def verify_trc20_payment(tx_hash: str, expected_amount: float, wallet: str) -> dict:
    """
    核验 TRC20 USDT 交易
    返回: {"ok": True/False, "amount": float, "from": str, "error": str}
    """
    url = f"{TRONGRID_BASE}/v1/transactions/{tx_hash}"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        if "data" not in data or not data["data"]:
            return {"ok": False, "error": "交易不存在"}

        tx = data["data"][0]

        # 检查交易状态
        if tx.get("ret", [{}])[0].get("contractRet") != "SUCCESS":
            return {"ok": False, "error": "交易未成功"}

        # 解析合约数据
        contract = tx.get("raw_data", {}).get("contract", [{}])[0]
        contract_type = contract.get("type", "")

        if contract_type != "TriggerSmartContract":
            return {"ok": False, "error": "不是 TRC20 交易"}

        value = contract.get("parameter", {}).get("value", {})
        to_address = value.get("contract_address", "")

        # USDT TRC20 合约地址
        USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

        # 解码 data 字段获取金额和接收方
        raw_data_hex = value.get("data", "")
        if len(raw_data_hex) < 136:
            return {"ok": False, "error": "无法解析交易数据"}

        # TRC20 transfer: a9059cbb + 接收地址(32字节) + 金额(32字节)
        recipient_hex = raw_data_hex[32:72]  # 接收地址后20字节
        amount_hex = raw_data_hex[72:136]    # 金额

        amount_raw = int(amount_hex, 16)
        amount_usdt = amount_raw / 1_000_000  # USDT 6位小数

        # 转换接收地址 (hex → base58)
        recipient_address = hex_to_tron_address("41" + recipient_hex)

        # 核验接收地址
        if recipient_address.lower() != wallet.lower():
            return {"ok": False, "error": f"收款地址不匹配"}

        # 核验金额（允许1%误差）
        if abs(amount_usdt - expected_amount) > expected_amount * 0.01:
            return {"ok": False, "error": f"金额不符: 收到 {amount_usdt:.2f} USDT，期望 {expected_amount} USDT"}

        sender_hex = tx.get("raw_data", {}).get("contract", [{}])[0].get("parameter", {}).get("value", {}).get("owner_address", "")
        sender = hex_to_tron_address(sender_hex) if sender_hex else "未知"

        return {
            "ok": True,
            "amount": amount_usdt,
            "from": sender
        }

    except asyncio.TimeoutError:
        return {"ok": False, "error": "区块链 API 超时，请稍后重试"}
    except Exception as e:
        logger.error(f"核验错误: {e}")
        return {"ok": False, "error": f"核验失败: {str(e)}"}


def hex_to_tron_address(hex_addr: str) -> str:
    """将 hex 地址转换为 Tron base58 地址"""
    import hashlib
    import base58

    if hex_addr.startswith("0x"):
        hex_addr = "41" + hex_addr[2:]

    addr_bytes = bytes.fromhex(hex_addr)
    hash1 = hashlib.sha256(addr_bytes).digest()
    hash2 = hashlib.sha256(hash1).digest()
    checksum = hash2[:4]
    return base58.b58encode(addr_bytes + checksum).decode()


async def notify_make_webhook(tg_chat_id: int, tg_username: str,
                               product_key: str, amount: float,
                               tx_hash: str):
    """通知 Make Webhook 触发自动发货"""
    product = PRODUCTS[product_key]
    payload = {
        "tg_chat_id": str(tg_chat_id),
        "tg_username": tg_username or "未知",
        "product": product["name"],
        "amount": str(amount),
        "tx_hash": tx_hash,
        "file_url": product["file_url"]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(MAKE_WEBHOOK_URL, json=payload,
                                     timeout=aiohttp.ClientTimeout(total=10)) as resp:
                logger.info(f"Make Webhook 响应: {resp.status}")
                return resp.status == 200
    except Exception as e:
        logger.error(f"Make Webhook 失败: {e}")
        return False


# ── Bot 命令处理 ────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    keyboard = []
    for key, p in PRODUCTS.items():
        keyboard.append([InlineKeyboardButton(
            f"🛒 {p['name']} — ${p['price_usdt']} USDT",
            callback_data=f"buy_{key}"
        )])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 欢迎！\n\n"
        "我们提供以下数字产品，支持 USDT (TRC20) 支付：\n\n"
        "请选择你想购买的产品 👇",
        reply_markup=reply_markup
    )


async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理购买按钮点击"""
    query = update.callback_query
    await query.answer()

    product_key = query.data.replace("buy_", "")
    if product_key not in PRODUCTS:
        await query.edit_message_text("❌ 产品不存在")
        return

    product = PRODUCTS[product_key]
    context.user_data["pending_product"] = product_key

    await query.edit_message_text(
        f"🛒 *{product['name']}*\n\n"
        f"📋 {product['description']}\n\n"
        f"💵 价格：*{product['price_usdt']} USDT (TRC20)*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📤 请转账至以下地址：\n\n"
        f"`{USDT_WALLET}`\n\n"
        f"⚠️ 请确认转账金额为 *{product['price_usdt']} USDT*，网络选择 *TRC20*\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ 转账完成后，请把 *交易Hash (TxHash)* 发给我\n"
        f"（在钱包 App 的交易记录里可以找到）",
        parse_mode="Markdown"
    )


async def handle_txhash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户发送的 TxHash"""
    text = update.message.text.strip()

    # 检查是否有待处理产品
    product_key = context.user_data.get("pending_product")
    if not product_key:
        await update.message.reply_text(
            "请先点击 /start 选择产品，再发送 TxHash。"
        )
        return

    # 简单检查 TxHash 格式 (TRC20: 64位hex)
    if len(text) != 64 or not all(c in "0123456789abcdefABCDEF" for c in text):
        await update.message.reply_text(
            "❌ 格式不对，TxHash 应该是 64 位的字母数字组合。\n"
            "请重新检查并发送正确的 TxHash。"
        )
        return

    # 防止重复发货
    if text in processed_tx:
        await update.message.reply_text("⚠️ 这笔交易已经处理过了。")
        return

    product = PRODUCTS[product_key]
    msg = await update.message.reply_text("🔍 正在核验区块链交易，请稍候...")

    # 核验交易
    result = await verify_trc20_payment(
        tx_hash=text,
        expected_amount=product["price_usdt"],
        wallet=USDT_WALLET
    )

    if not result["ok"]:
        await msg.edit_text(
            f"❌ 核验失败：{result['error']}\n\n"
            f"如有疑问请联系客服。"
        )
        return

    # 核验成功 - 标记已处理
    processed_tx.add(text)
    context.user_data.pop("pending_product", None)

    await msg.edit_text("✅ 付款已确认！正在发货，请稍候...")

    # 通知 Make Webhook
    tg_user = update.effective_user
    success = await notify_make_webhook(
        tg_chat_id=tg_user.id,
        tg_username=tg_user.username or tg_user.first_name,
        product_key=product_key,
        amount=result["amount"],
        tx_hash=text
    )

    if not success:
        # Make 失败时直接发货作为备份
        await update.message.reply_text(
            f"✅ 付款已确认 ({result['amount']} USDT)\n\n"
            f"📦 你的产品链接：\n{product['file_url']}\n\n"
            f"感谢购买！"
        )
    # Make 成功时，Make 会自动发文件给用户


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通文本消息"""
    text = update.message.text.strip()

    # 如果看起来像 TxHash，转给 handle_txhash
    if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text):
        await handle_txhash(update, context)
        return

    await update.message.reply_text(
        "请发送 /start 开始购买流程。\n"
        "购买后，请将 TxHash 发给我完成核验。"
    )


# ── 主程序 ──────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot 启动中...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
