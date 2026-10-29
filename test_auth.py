import base64
import io
import unittest
from unittest.mock import patch
from registry import get_json, Registry

class AuthTests(unittest.TestCase):
    def test_basic_header_and_get(self):
        with patch('registry.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value=io.BytesIO(b'{"result":[]}')
            self.assertEqual(get_json('https://instance.invalid','/api/now/table/sn_aia_agent',('user','pass'),{}),{'result':[]})
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(),'GET')
            self.assertEqual(request.get_header('Authorization'),'Basic '+base64.b64encode(b'user:pass').decode())
    def test_basic_configuration_live_and_incomplete(self):
        env={'SERVICENOW_INSTANCE_URL':'https://instance.invalid','SERVICENOW_USERNAME':'user','SERVICENOW_PASSWORD':'pass'}
        result=Registry(env,lambda *args:{'result':[]}).refresh()
        self.assertEqual(result['sources'][1]['mode'],'live')
        del env['SERVICENOW_PASSWORD']
        self.assertEqual(Registry(env).refresh()['sources'][1]['health'],'error')
