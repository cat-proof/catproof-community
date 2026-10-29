import unittest
from unittest.mock import patch,Mock
from urllib.error import URLError
from network_policy import public_addresses,PublicHTTPSConnection,hosted_egress,build_opener,HOSTED

class EgressTests(unittest.TestCase):
    def address(self,ip):return [(2,1,6,'',(ip,443))]
    def test_private_and_special_use_destinations_fail_closed(self):
        for ip in ('127.0.0.1','10.1.2.3','169.254.169.254','192.168.1.1','0.0.0.0','::1','::ffff:127.0.0.1'):
            with patch('socket.getaddrinfo',return_value=self.address(ip)),self.assertRaises(URLError):public_addresses('source.example',443)
        with self.assertRaises(URLError):public_addresses('source.example',8443)
    def test_mixed_public_private_resolution_is_rejected(self):
        with patch('socket.getaddrinfo',return_value=self.address('93.184.216.34')+self.address('127.0.0.1')),self.assertRaises(URLError):public_addresses('source.example',443)
    def test_checked_address_is_pinned_for_connection_and_context_is_scoped(self):
        sock=Mock()
        def connect(conn):conn._create_connection(('source.example',443),2)
        with patch('socket.getaddrinfo',return_value=self.address('93.184.216.34')) as dns,patch('socket.socket',return_value=sock),patch('http.client.HTTPSConnection.connect',connect):PublicHTTPSConnection('source.example').connect()
        dns.assert_called_once();sock.connect.assert_called_once_with(('93.184.216.34',443))
        self.assertFalse(HOSTED.get())
        with hosted_egress():
            self.assertTrue(HOSTED.get())
            with self.assertRaises(URLError):build_opener().open('http://127.0.0.1/',timeout=1)
        self.assertFalse(HOSTED.get())
