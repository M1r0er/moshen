"""配置密钥保护回归测试

覆盖四件事：
1. 明文密钥不出后端（GET /api/config 一律掩码）
2. 保存时不丢密钥（留空/回传掩码 = 保持原值）
3. 仍能删除密钥（clear_keys 显式清除）
4. 「应用渠道到职能」在服务端完成，密钥不下发浏览器

运行（仓库根目录，无需额外依赖）：
    python -m unittest discover -s backend/tests -t backend -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.config import ConfigManager, MODEL_ROLES, MASKED_KEY  # noqa: E402


def _clear_env():
    """清掉上个用例残留的环境变量（ConfigManager 经 os.environ 读取配置）"""
    for key in list(os.environ):
        if key.startswith(("DEFAULT_API_", "IMAGE_")) or key in ("API_CHANNELS", "INDEPENDENT_API_KEYS"):
            os.environ.pop(key, None)
    for role in MODEL_ROLES:
        for suffix in ("MODELS", "MODEL", "BASE_URL", "API_KEY", "TEMPERATURE", "MAX_TOKENS"):
            os.environ.pop(f"{role}_{suffix}", None)


class ConfigSecretTest(unittest.TestCase):
    def setUp(self):
        _clear_env()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = str(Path(self.tmp.name) / ".env")
        self.mgr = ConfigManager(env_path=self.env_path)

    # ---------- 辅助 ----------

    def _seed(self):
        """落一份"已经存有密钥"的配置"""
        self.mgr.save_config({
            "independent_keys": True,
            "roles": {
                "TEXT_MASTER": {"models": ["m1"], "base_url": "https://a/v1", "api_key": "role-key-A"},
                "DIALOGUE_PARTNER": {"models": ["m2"], "base_url": "https://b/v1", "api_key": "role-key-B"},
            },
            "api_channels": [
                {"id": "ch_1", "name": "渠道一", "models": ["m1"], "base_url": "https://a/v1",
                 "api_key": "chan-key-1", "is_default": True},
                {"id": "ch_2", "name": "渠道二", "models": ["m2"], "base_url": "https://b/v1",
                 "api_key": "chan-key-2", "is_default": False},
            ],
            "image_config": {"enabled": True, "base_url": "https://img/v1", "api_key": "img-key",
                             "model": "dall-e", "size": "1024x1024", "quality": "auto"},
        })

    def _raw_env(self) -> str:
        return Path(self.env_path).read_text(encoding="utf-8")

    def _channel_keys(self) -> dict:
        return {c["id"]: c["api_key"] for c in self.mgr.api_channels}

    # ---------- 1. 明文不出后端 ----------

    def test_get_full_config_masks_all_keys(self):
        self._seed()
        cfg = self.mgr.get_full_config()
        self.assertEqual(cfg["default_api"]["api_key"], MASKED_KEY)
        self.assertEqual(cfg["roles"]["TEXT_MASTER"]["api_key"], MASKED_KEY)
        self.assertEqual(cfg["roles"]["DIALOGUE_PARTNER"]["api_key"], MASKED_KEY)
        self.assertEqual(cfg["image_config"]["api_key"], MASKED_KEY)
        for ch in cfg["api_channels"]:
            self.assertEqual(ch["api_key"], MASKED_KEY)

        blob = json.dumps(cfg, ensure_ascii=False)
        for leak in ("role-key-A", "role-key-B", "chan-key-1", "chan-key-2", "img-key"):
            self.assertNotIn(leak, blob)

    def test_masked_config_still_reports_configured(self):
        """掩码保留"非空"语义，前端沿用 api_key 真值判断仍成立"""
        self._seed()
        cfg = self.mgr.get_full_config()
        self.assertTrue(cfg["roles"]["TEXT_MASTER"]["is_configured"])
        self.assertTrue(cfg["default_api"]["is_configured"])
        self.assertTrue(all(ch["api_key"] for ch in cfg["api_channels"]))
        # 未配置的职能仍为空串
        self.assertEqual(cfg["roles"]["NOVEL_ANALYZER"]["api_key"], "")
        self.assertFalse(cfg["roles"]["NOVEL_ANALYZER"]["is_configured"])

    # ---------- 2. 保存不丢密钥 ----------

    def test_save_masked_payload_preserves_secrets(self):
        """模拟真实前端：拿掩码配置改个模型名再存回，密钥必须原样保留"""
        self._seed()
        posted = self.mgr.get_full_config()
        posted["roles"]["TEXT_MASTER"]["models"] = ["m1", "m1b"]
        self.mgr.save_config(posted)

        self.assertEqual(self.mgr._models["TEXT_MASTER"].api_key, "role-key-A")
        self.assertEqual(self.mgr._models["TEXT_MASTER"].models, ["m1", "m1b"])
        self.assertEqual(self.mgr._models["DIALOGUE_PARTNER"].api_key, "role-key-B")
        self.assertEqual(self._channel_keys(), {"ch_1": "chan-key-1", "ch_2": "chan-key-2"})
        self.assertEqual(self.mgr.image_config["api_key"], "img-key")
        self.assertIn("role-key-A", self._raw_env())

    def test_save_blank_keys_preserves_secrets(self):
        """输入框留空（前端不再回填明文）也必须保持原值"""
        self._seed()
        self.mgr.save_config({
            "independent_keys": True,
            "roles": {"TEXT_MASTER": {"models": ["m1"], "base_url": "https://a/v1", "api_key": ""}},
            "api_channels": [
                {"id": "ch_1", "name": "渠道一", "models": ["m1"], "base_url": "https://a/v1",
                 "api_key": "", "is_default": True},
            ],
            "image_config": {"enabled": True, "base_url": "https://img/v1", "api_key": ""},
        })
        self.assertEqual(self.mgr._models["TEXT_MASTER"].api_key, "role-key-A")
        self.assertEqual(self.mgr.api_channels[0]["api_key"], "chan-key-1")
        self.assertEqual(self.mgr.image_config["api_key"], "img-key")

    def test_save_new_keys_updates(self):
        self._seed()
        self.mgr.save_config({
            "independent_keys": True,
            "roles": {"TEXT_MASTER": {"models": ["m1"], "base_url": "https://a/v1",
                                      "api_key": "brand-new"}},
            "api_channels": [
                {"id": "ch_1", "name": "渠道一", "models": ["m1"], "base_url": "https://a/v1",
                 "api_key": "chan-new", "is_default": True},
            ],
            "image_config": {"enabled": True, "base_url": "https://img/v1", "api_key": "img-new"},
        })
        self.assertEqual(self.mgr._models["TEXT_MASTER"].api_key, "brand-new")
        self.assertEqual(self.mgr.api_channels[0]["api_key"], "chan-new")
        self.assertEqual(self.mgr.image_config["api_key"], "img-new")
        # 默认 API 由默认渠道派生
        self.assertEqual(self.mgr._default_api.api_key, "chan-new")

    # ---------- 3. 仍能删除密钥 ----------

    def test_clear_keys_removes_only_targets(self):
        self._seed()
        self.mgr.save_config({
            "independent_keys": True,
            "roles": {"TEXT_MASTER": {"models": ["m1"], "base_url": "https://a/v1", "api_key": ""}},
            "api_channels": [
                {"id": "ch_1", "name": "渠道一", "models": ["m1"], "base_url": "https://a/v1",
                 "api_key": "", "is_default": True},
                {"id": "ch_2", "name": "渠道二", "models": ["m2"], "base_url": "https://b/v1",
                 "api_key": ""},
            ],
            "image_config": {"enabled": True, "base_url": "https://img/v1", "api_key": ""},
            "clear_keys": ["role:TEXT_MASTER", "image", "channel:ch_2"],
        })
        self.assertEqual(self.mgr._models["TEXT_MASTER"].api_key, "")
        self.assertEqual(self.mgr.image_config["api_key"], "")
        keys = self._channel_keys()
        self.assertEqual(keys["ch_2"], "")
        self.assertEqual(keys["ch_1"], "chan-key-1")   # 未列入 clear_keys 的保持原值

        raw = self._raw_env()
        self.assertNotIn("img-key", raw)
        self.assertNotIn("role-key-A", raw)
        self.assertIn("chan-key-1", raw)

    def test_clear_default_key(self):
        self._seed()
        cfg = self.mgr.get_full_config()
        cfg["clear_keys"] = ["default"]
        self.mgr.save_config(cfg)

        self.assertEqual(self.mgr._default_api.api_key, "")
        keys = self._channel_keys()
        self.assertEqual(keys["ch_1"], "")          # 默认渠道的密钥被清空
        self.assertEqual(keys["ch_2"], "chan-key-2")
        # 重建后不残留（默认 API 由渠道派生，清到渠道上才真正生效）
        self.assertEqual(ConfigManager(env_path=self.env_path)._default_api.api_key, "")

    # ---------- 4. UI 专用字段不落盘 ----------

    def test_ui_only_fields_not_persisted(self):
        self._seed()
        cfg = self.mgr.get_full_config()
        for ch in cfg["api_channels"]:
            ch["_hasKey"] = True
            ch["_clearKey"] = False
        cfg["roles"]["TEXT_MASTER"]["_hasKey"] = True
        cfg["image_config"]["_hasKey"] = True
        self.mgr.save_config(cfg)

        raw = self._raw_env()
        self.assertNotIn("_hasKey", raw)
        self.assertNotIn("_clearKey", raw)
        channels_line = [l for l in raw.splitlines() if l.startswith("API_CHANNELS=")][0]
        channels = json.loads(channels_line.split("=", 1)[1])
        self.assertTrue(all(not str(k).startswith("_") for c in channels for k in c))
        self.assertEqual({c["api_key"] for c in channels}, {"chan-key-1", "chan-key-2"})

    # ---------- 5. 应用渠道到职能（服务端） ----------

    def test_apply_channel_to_role_copies_key_without_leaking(self):
        self._seed()
        result = self.mgr.apply_channel_to_role("NOVEL_ANALYZER", "ch_2")

        self.assertEqual(self.mgr._models["NOVEL_ANALYZER"].api_key, "chan-key-2")
        self.assertEqual(self.mgr._models["NOVEL_ANALYZER"].base_url, "https://b/v1")
        self.assertEqual(self.mgr._models["NOVEL_ANALYZER"].models, ["m2"])
        self.assertEqual(result["roles"]["NOVEL_ANALYZER"]["api_key"], MASKED_KEY)
        self.assertTrue(result["roles"]["NOVEL_ANALYZER"]["is_configured"])
        self.assertNotIn("chan-key-2", json.dumps(result, ensure_ascii=False))

    def test_apply_channel_by_name_and_rejects_unknown(self):
        self._seed()
        self.mgr.apply_channel_to_role("TEXT_MASTER", "渠道二")
        self.assertEqual(self.mgr._models["TEXT_MASTER"].api_key, "chan-key-2")

        with self.assertRaises(ValueError):
            self.mgr.apply_channel_to_role("NOT_A_ROLE", "ch_1")
        with self.assertRaises(ValueError):
            self.mgr.apply_channel_to_role("TEXT_MASTER", "不存在的渠道")

    # ---------- 6. 落盘后重建一致 ----------

    def test_round_trip_after_reload(self):
        self._seed()
        m2 = ConfigManager(env_path=self.env_path)
        self.assertEqual(m2._models["TEXT_MASTER"].api_key, "role-key-A")
        self.assertEqual(m2._models["DIALOGUE_PARTNER"].api_key, "role-key-B")
        self.assertEqual(m2.image_config["api_key"], "img-key")
        self.assertEqual({c["id"]: c["api_key"] for c in m2.api_channels},
                         {"ch_1": "chan-key-1", "ch_2": "chan-key-2"})
        self.assertEqual(m2._default_api.api_key, "chan-key-1")


if __name__ == "__main__":
    unittest.main()
