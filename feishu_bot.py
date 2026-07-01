"""
飞书 Webhook 机器人客户端
用于推送交易信号、开单成功/失败等通知
"""
import requests
from loguru import logger
import os
from dotenv import load_dotenv

load_dotenv()


class FeishuBot:
    """飞书 Webhook 机器人"""

    def __init__(self, webhook_url=None):
        self.webhook_url = webhook_url or os.getenv("FEISHU_WEBHOOK", "")
        self.enabled = bool(self.webhook_url)
        if self.enabled:
            logger.info("飞书机器人已启用")
        else:
            logger.debug("飞书机器人未配置")

    def send_text(self, text):
        """发送文本消息"""
        if not self.enabled:
            logger.debug("飞书机器人未配置，跳过通知")
            return False

        try:
            payload = {
                "msg_type": "text",
                "content": {"text": text}
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            result = resp.json()
            if result.get("StatusCode") == 0:
                logger.info(f"飞书通知成功: {text[:50]}...")
                return True
            else:
                logger.warning(f"飞书通知失败: {result}")
                return False
        except Exception as e:
            logger.error(f"飞书通知异常: {e}")
            return False

    def send_card(self, title, content, color="blue"):
        """发送卡片消息"""
        if not self.enabled:
            return False

        try:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": color
                    },
                    "elements": [
                        {"tag": "div", "text": {"tag": "plain_text", "content": content}}
                    ]
                }
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            result = resp.json()
            if result.get("StatusCode") == 0:
                logger.info(f"飞书卡片成功: {title}")
                return True
            else:
                logger.warning(f"飞书卡片失败: {result}")
                return False
        except Exception as e:
            logger.error(f"飞书卡片异常: {e}")
            return False

    def notify_trade(self, tf, direction, amount, price, tp, sl, success=True, leverage=100):
        """发送交易通知"""
        direction_text = "做多" if direction == "long" else "做空"
        
        if success:
            color = "green"
            title = f"✅ [{tf}] 开单成功"
            content = f"""方向: {direction_text}
金额: {amount}U
杠杆: {leverage}x
价格: {price}
止盈: {tp}
止损: {sl}
时间: {self._get_now()}"""
        else:
            color = "red"
            title = f"❌ [{tf}] 开单失败"
            content = f"""方向: {direction_text}
金额: {amount}U
杠杆: {leverage}x
时间: {self._get_now()}"""

        return self.send_card(title, content, color)

    def notify_buffer_reset(self, tf, price):
        """缓冲带重置通知"""
        text = f"""🔄 [{tf}] 价格走出缓冲带，重置预警
价格: {price}
时间: {self._get_now()}"""
        return self.send_text(text)

    def notify_signal(self, tf, direction, entry_index, num_entries, price):
        """信号触发通知"""
        direction_text = "做多" if direction == "long" else "做空"
        text = f"""📊 [{tf}] {direction_text}信号触发
入场: {entry_index}/{num_entries}
价格: {price}
时间: {self._get_now()}"""
        return self.send_text(text)

    def notify_auto_start(self):
        """自动交易启动通知"""
        return self.send_card("🚀 自动交易已启动", "系统开始监控EMA区间信号", "blue")

    def notify_auto_stop(self):
        """自动交易停止通知"""
        return self.send_card("⏹️ 自动交易已停止", "系统停止监控", "grey")

    def notify_error(self, tf, error_msg):
        """错误通知"""
        return self.send_card(f"⚠️ [{tf}] 异常", error_msg, "red")

    def _get_now(self):
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 全局单例
_feishu_bot = None


def get_feishu_bot(webhook_url=None):
    """获取飞书机器人实例"""
    global _feishu_bot
    if _feishu_bot is None:
        _feishu_bot = FeishuBot(webhook_url)
    return _feishu_bot