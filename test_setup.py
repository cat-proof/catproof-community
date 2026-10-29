import json
import unittest
from unittest.mock import patch
from adapters.setup import check_setup,TOP_FIVE,GUIDE_FAMILIES
from adapters.readers import read_connector
from adapters.transport import Response
from test_connectors import FakeTransport
from adapters.catalog import validate_config

class SetupTests(unittest.TestCase):
    def connection(self,kind='snowflake',config=None,prefix='SF',enabled=True):
        return dict(id='conn',kind=kind,config=({'database':'DB','schema':'PUBLIC'} if config is None else config),credential_prefix=prefix,enabled=enabled)
    def test_top_five_are_explicit_and_have_guides(self):
        self.assertEqual(len(TOP_FIVE),5)
        self.assertTrue(all(k in GUIDE_FAMILIES for k in TOP_FIVE))
    def test_missing_token_is_actionable_and_never_echoes_values(self):
        c=self.connection();self.assertFalse(check_setup(c,{})['ready'])
        result=check_setup(c,{'SF_TOKEN':'private-test-value'})
        self.assertTrue(result['ready']);self.assertNotIn('private-test-value',json.dumps(result));self.assertNotIn('SF_TOKEN',json.dumps(result))
    def test_paused_connection_not_ready(self):
        self.assertFalse(check_setup(self.connection(enabled=False),{'SF_TOKEN':'private'})['ready'])
    def test_aws_profile_or_complete_pair_only(self):
        c=self.connection('aws_bedrock',{'region':'us-east-1'},'AWS')
        with patch('adapters.setup.importlib.util.find_spec',return_value=object()):
            self.assertTrue(check_setup(c,{'AWS_PROFILE':'reader'})['ready'])
            self.assertFalse(check_setup(c,{'AWS_ACCESS_KEY_ID':'key'})['ready'])
            self.assertFalse(check_setup(c,{'AWS_PROFILE':'reader','AWS_ACCESS_KEY_ID':'key','AWS_SECRET_ACCESS_KEY':'secret'})['ready'])
    def test_optional_auth_does_not_hide_missing_explicit_token(self):
        self.assertTrue(check_setup(self.connection('a2a',{},'',True),{})['ready'])
        self.assertFalse(check_setup(self.connection('a2a',{},'A2A',True),{})['ready'])
    def test_snowflake_role_and_google_quota_headers(self):
        for kind,config,body,header,value in [
            ('snowflake',{'database':'DB','schema':'PUBLIC','role':'READER'},[],'X-Snowflake-Role','READER'),
            ('google_registry',{'project':'p','location':'global','billing_project':'quota'},{'agents':[]},'X-Goog-User-Project','quota')]:
            captured={};transport=FakeTransport([body])
            def factory(origin,headers):captured.update(headers);return transport
            read_connector({'kind':kind,'url':'https://example.invalid','credential_prefix':'X','config':config},{'X_TOKEN':'private'},transport_factory=factory)
            self.assertEqual(captured[header],value)
    def test_configuration_rejects_header_newlines(self):
        with self.assertRaises(ValueError):validate_config('snowflake',{'database':'DB','schema':'PUBLIC','role':'ROLE\nInjected: value'})
