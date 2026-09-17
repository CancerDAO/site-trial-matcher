import json
import unittest
from unittest.mock import patch

from china_trial_demo.model_api import call_minimax_prompt


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ModelApiTests(unittest.TestCase):
    @patch("china_trial_demo.model_api.ssl.create_default_context", return_value=None)
    @patch("china_trial_demo.model_api.urllib.request.urlopen")
    def test_kimi_uses_supported_temperature_and_token_parameter(self, urlopen, _ssl_context):
        urlopen.return_value = _Response({
            "model": "k3",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"total_tokens": 10},
        })
        env = {
            "SITE_TRIAL_MODEL_API_KEY": "test-key",
            "SITE_TRIAL_MODEL_BASE_URL": "https://api.kimi.com/coding/v1",
            "SITE_TRIAL_MODEL_NAME": "k3",
        }
        with patch.dict("os.environ", env, clear=True):
            payload, meta = call_minimax_prompt("return json", max_completion_tokens=512)
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["temperature"], 1.0)
        self.assertEqual(body["max_tokens"], 512)
        self.assertNotIn("max_completion_tokens", body)
        self.assertEqual(request.headers["User-agent"], "CancerDAO-TrialMatcher/0.3")
        self.assertTrue(payload["ok"])
        self.assertEqual(meta["usage"]["total_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
