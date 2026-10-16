from email.message import Message
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from weir.server import BodyError, MAX_BODY_BYTES, _read_body


def request(length="0", body=b"", extra=None):
    headers = Message()
    headers["content-length"] = length
    for name, value in extra or []:
        headers[name] = value
    return SimpleNamespace(headers=headers, rfile=BytesIO(body), connection=Mock())


class BodyLimitTests(unittest.TestCase):
    def test_oversized_request_is_rejected_without_reading(self):
        req = request(str(MAX_BODY_BYTES + 1))
        req.rfile = Mock()
        with self.assertRaises(BodyError) as caught:
            _read_body(req)
        self.assertEqual(caught.exception.status, 413)
        req.rfile.read.assert_not_called()

    def test_ambiguous_framing_is_rejected(self):
        for extra in [[("content-length", "1")], [("transfer-encoding", "chunked")]]:
            with self.assertRaises(BodyError) as caught:
                _read_body(request(extra=extra))
            self.assertEqual(caught.exception.status, 400)

    def test_invalid_and_incomplete_bodies_are_rejected(self):
        for length in ["-1", "2.0", "abc", "١"]:
            with self.assertRaises(BodyError):
                _read_body(request(length))
        with self.assertRaises(BodyError):
            _read_body(request("3", b"ab"))

    def test_limit_boundary_is_accepted(self):
        self.assertEqual(_read_body(request("3", b"abc"), 3), b"abc")

    def test_timeout_is_rejected(self):
        req = request("3")
        req.rfile = Mock()
        req.rfile.read.side_effect = TimeoutError
        with self.assertRaises(BodyError) as caught:
            _read_body(req)
        self.assertEqual(caught.exception.status, 408)
