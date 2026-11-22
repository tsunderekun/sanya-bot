import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from aiogram import types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from sanya.database import Database
from sanya.handlers.draw_access import cmd_drawrevoke, decide_group_draw
from sanya.handlers.media import cmd_draw
from sanya.repository import Repository
from sanya.services.image_generation import ImageGeneration


class DrawAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.db = await self.enterAsyncContext(Database(Path(folder) / "draw.db"))
        self.enterContext(patch.dict("os.environ", {"IMAGE_GENERATION_APPROVER_ID": "99"}))
        self.group = NS(
            chat=NS(id=-100, type="supergroup", title="Тестовая конфа"),
            from_user=NS(id=42, is_bot=False, full_name="Тестер"), sender_chat=None,
            text="/нарисуй секретное описание", bot=NS(send_message=AsyncMock()),
            reply=AsyncMock(), reply_photo=AsyncMock(),
        )
        self.control = types.Message.model_validate({
            "message_id": 1, "date": 0, "chat": {"id": 99, "type": "private"},
        })
        self.service = Mock()

    async def request(self):
        await cmd_draw(self.group, self.service, self.db)
        return self.group.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0]

    async def click(self, data, actor=99):
        callback = NS(message=self.control, from_user=NS(id=actor), data=data, answer=AsyncMock())
        with patch.object(types.Message, "edit_text", new_callable=AsyncMock) as edit:
            await decide_group_draw(callback, self.db)
        return edit

    async def test_two_buttons_owner_only_group_scoped_and_revocable(self):
        allow, deny = await self.request()
        self.assertEqual((allow.text, deny.text), ("Разрешить", "Отказать"))
        destination, notice = self.group.bot.send_message.await_args.args
        self.assertEqual(destination, 99)
        self.assertNotIn("секретное описание", notice)
        await cmd_draw(self.group, self.service, self.db)
        self.group.bot.send_message.assert_awaited_once()
        self.service.start.assert_not_called()
        await self.click(allow.callback_data, actor=42)
        self.assertEqual(await self.db.run(Repository.get_draw_access, -100, 42), "pending")
        edit = await self.click(allow.callback_data)
        revoke = edit.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        await cmd_draw(self.group, self.service, self.db)
        self.service.start.assert_called_once_with(self.group, "секретное описание")
        self.group.chat.id = -200
        await cmd_draw(self.group, self.service, self.db)
        self.service.start.assert_called_once()
        await self.click(revoke.callback_data)
        await self.click(allow.callback_data)
        self.assertEqual(await self.db.run(Repository.get_draw_access, -100, 42), "revoked")

    async def test_denial_does_not_resend_and_command_revokes_pending(self):
        _, deny = await self.request()
        await self.click(deny.callback_data)
        await cmd_draw(self.group, self.service, self.db)
        self.group.bot.send_message.assert_awaited_once()
        self.service.start.assert_not_called()
        self.group.from_user.id = 43
        allow, _ = await self.request()
        message = NS(chat=self.control.chat, from_user=NS(id=99), reply=AsyncMock())
        await cmd_drawrevoke(message, NS(args="-100 43"), self.db)
        await self.click(allow.callback_data)
        self.assertEqual(await self.db.run(Repository.get_draw_access, -100, 43), "revoked")

    async def test_failed_notice_can_be_retried_and_safe_mode_does_not_notify(self):
        self.group.bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id=99, text="test"), message="test",
        )
        await cmd_draw(self.group, self.service, self.db)
        self.assertIsNone(await self.db.run(Repository.get_draw_access, -100, 42))
        self.group.bot.send_message.side_effect = None
        self.group.bot.send_message.reset_mock()
        await self.db.run(Repository.set_chat_reply_mode, -100, "safe")
        await cmd_draw(self.group, self.service, self.db)
        self.group.bot.send_message.assert_not_called()
        self.service.start.assert_not_called()

    async def test_revocation_during_rewrite_prevents_provider_call(self):
        allow, _ = await self.request()
        await self.click(allow.callback_data)

        async def rewrite(prompt):
            await self.db.run(Repository.revoke_draw_access, -100, 42)
            return prompt

        provider = NS(generate=AsyncMock())
        service = ImageGeneration(provider, self.db, NS(rewrite_image_prompt=rewrite))
        await cmd_draw(self.group, service, self.db)
        await service._task
        provider.generate.assert_not_called()
        self.group.reply_photo.assert_not_called()

    async def test_revocation_during_generation_suppresses_photo(self):
        allow, _ = await self.request()
        await self.click(allow.callback_data)

        async def generate(prompt):
            await self.db.run(Repository.revoke_draw_access, -100, 42)
            return b"image"

        llm = NS(rewrite_image_prompt=AsyncMock(return_value=""))
        service = ImageGeneration(NS(generate=generate), self.db, llm)
        await cmd_draw(self.group, service, self.db)
        await service._task
        self.group.reply_photo.assert_not_called()

    async def test_no_approver_keeps_group_generation_disabled(self):
        with patch.dict("os.environ", {"IMAGE_GENERATION_APPROVER_ID": ""}):
            await cmd_draw(self.group, self.service, self.db)
        self.group.bot.send_message.assert_not_called()
        self.service.start.assert_not_called()
