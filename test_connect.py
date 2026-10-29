import contextlib
import io
import unittest
from unittest.mock import patch
import connect

class ConnectTests(unittest.TestCase):
    def test_databricks_only_clears_other_credentials(self):
        with patch.dict('os.environ', {'SERVICENOW_INSTANCE_URL':'https://unused.invalid','SERVICENOW_TOKEN':'unused'}, clear=True), patch('connect.getpass.getpass', return_value='private-token'), patch('connect.Registry') as registry, patch('connect.os.execve') as launch, contextlib.redirect_stdout(io.StringIO()):
            registry.return_value.refresh.return_value={'sources':[{'name':'Databricks','health':'ok','mode':'live','error':None},{'name':'ServiceNow','health':'ok','mode':'seeded','error':None}], 'agents':[]}
            self.assertEqual(connect.main(['--source','databricks','--host','https://workspace.invalid','--check-only']),0)
            env=registry.call_args.args[0]
            self.assertNotIn('SERVICENOW_TOKEN',env)
            self.assertNotIn('SERVICENOW_INSTANCE_URL',env)
            self.assertEqual(env['DATABRICKS_HOST'],'https://workspace.invalid')
            launch.assert_not_called()

    def test_failed_live_check_blocks_launch(self):
        with patch.dict('os.environ', {}, clear=True), patch('connect.getpass.getpass', return_value='private-token'), patch('connect.Registry') as registry, patch('connect.os.execve') as launch, contextlib.redirect_stdout(io.StringIO()) as output:
            registry.return_value.refresh.return_value={'sources':[{'name':'Databricks','health':'error','mode':'live','error':'HTTP 403'}], 'agents':[]}
            self.assertEqual(connect.main(['--source','databricks','--host','https://workspace.invalid']),1)
            launch.assert_not_called()
            self.assertNotIn('private-token',output.getvalue())

    def test_basic_auth_prompts_and_preserves_password(self):
        with patch.dict('os.environ', {'SERVICENOW_TOKEN':'ignore-old-token'}, clear=True), patch('connect.input', return_value='demo-user'), patch('connect.getpass.getpass', return_value=' password '), patch('connect.Registry') as registry, contextlib.redirect_stdout(io.StringIO()) as output:
            registry.return_value.refresh.return_value={'sources':[{'name':'ServiceNow','health':'ok','mode':'live','error':None}], 'agents':[]}
            self.assertEqual(connect.main(['--source','servicenow','--host','https://instance.invalid','--servicenow-auth','basic','--check-only']),0)
            env=registry.call_args.args[0]
            self.assertNotIn('SERVICENOW_TOKEN',env)
            self.assertEqual(env['SERVICENOW_PASSWORD'],' password ')
            self.assertNotIn(' password ',output.getvalue())
