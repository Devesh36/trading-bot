import logging
import httpx


class TelegramNotifier:
    def __init__(self, config):
        self.token = config.telegram_bot_token
        self.chat = config.telegram_chat_id

    async def send(self, event, details=""):
        if not self.token or not self.chat:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat, "text": f"{event}\n{details}"[:4000]},
                )
                if r.status_code != 200:
                    logging.getLogger("bot").warning(
                        "NOTIFICATION_FAILED HTTP %s", r.status_code
                    )
        except Exception:
            # Transport exceptions can embed URLs containing the bot token.
            logging.getLogger("bot").warning("NOTIFICATION_FAILED transport error")
