import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('local_collector',Path(__file__).parent/'local/collector.py');collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)

class CollectorTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.state=self.root/'state'
    def tearDown(self):self.tmp.cleanup()
    def test_discovery_only_selected_names_and_versions_no_sensitive_config(self):
        home=self.root/'home';(home/'.codex/agents').mkdir(parents=True);(home/'.claude/agents').mkdir(parents=True)
        (home/'.codex/agents/reviewer.toml').write_text('instructions = "do not upload private instructions"')
        (home/'.claude/agents/researcher.md').write_text('private agent instructions')
        (home/'.codex/config.toml').write_text('[mcp_servers.docs]\nurl="https://private.example"\nheaders={Authorization="private credential"}\n[agents]\nmax_threads=4\n[agents.tester]\nconfig_file="/private/path.toml"\n')
        (home/'.claude.json').write_text(json.dumps({'mcpServers':{'database':{'env':{'PASSWORD':'private password'},'command':'privatecommand'}},'projects':{'/private/project':{'mcpServers':{'hidden':{}}}}}))
        runner=lambda *a,**kw:SimpleNamespace(stdout='codex-cli 1.2.3\nprivate extra output',returncode=0)
        rows=collector.discover(home,True,which=lambda x:'/bin/'+x,runner=runner)
        self.assertEqual(rows[0]['agents'],['reviewer','tester']);self.assertEqual(rows[0]['mcp_servers'],['docs']);self.assertEqual(rows[1]['agents'],['researcher'])
        self.assertEqual(rows[1]['mcp_servers'],['database']);self.assertNotIn('private',str(rows));self.assertNotIn('hidden',str(rows));self.assertEqual(rows[0]['version'],'1.2.3')
        with patch.object(collector,'safe_json',side_effect=AssertionError('Config read without opt-in')):
            rows=collector.discover(home,False,which=lambda x:None)
        self.assertEqual(rows[0]['agents'],[])
    def test_event_filters_and_hashes_session_without_reading_transcript(self):
        event={'session_id':'private-session-id','hook_event_name':'UserPromptSubmit','prompt':'private prompt','cwd':'/private/project','transcript_path':'/does/not/exist','tool_input':{'password':'private'}}
        collector.record_event(self.state,'codex',event);events=collector.activity(self.state)
        self.assertEqual(len(events),1);self.assertEqual(set(events[0]),{'id','tool','event','at'});self.assertNotIn('private',str(events));self.assertEqual(len(events[0]['id']),64)
        collector.record_event(self.state,'codex',{**event,'hook_event_name':'Stop'});self.assertEqual(len(collector.activity(self.state)),1)
        collector.record_event(self.state,'claude',{**event,'hook_event_name':'Stop'});self.assertEqual(len(collector.activity(self.state)),2)
    def test_hook_merge_idempotent_preserves_existing_and_removes_only_ours(self):
        home=self.root/'home';path=home/'.claude/settings.json';path.parent.mkdir(parents=True)
        existing={'permissions':{'deny':['Read(secret)']},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo existing'}]}]}}
        path.write_text(json.dumps(existing))
        collector.install_hooks(self.state,'claude',home);collector.install_hooks(self.state,'claude',home)
        data=json.loads(path.read_text());self.assertEqual(data['permissions'],existing['permissions']);self.assertEqual(len(data['hooks']['Stop']),2)
        collector.install_hooks(self.state,'claude',home,remove=True);data=json.loads(path.read_text());self.assertEqual(data['hooks']['Stop'],existing['hooks']['Stop'])
        self.assertEqual(len(list(path.parent.glob('*.catproof-backup-*'))),3)
    def test_private_config_permissions_sequence_persisted_on_uncertain_delivery(self):
        collector.private_dir(self.state);collector.save(self.state/'connection.json',{'server':'https://app.catproof.ai','token':'fixture-only-not-a-provider-key','sequence':0})
        self.assertEqual(os.stat(self.state/'connection.json').st_mode&0o777,0o600)
        def failed(*a):raise collector.LocalError('Network failed')
        with patch.object(collector,'discover',return_value=[]),self.assertRaises(collector.LocalError):collector.report(self.state,send=failed)
        self.assertEqual(collector.config(self.state)['sequence'],1)
        with patch.object(collector,'discover',return_value=[]):collector.report(self.state,send=lambda server,path,payload,token:self.assertEqual(payload['sequence'],2))
        os.chmod(self.state/'connection.json',0o644)
        with self.assertRaises(collector.LocalError):collector.config(self.state)
    def test_https_origin_and_symlinks_fail_closed(self):
        for url in ('http://localhost','https://user:pass@app.catproof.ai','https://app.catproof.ai/path','https://app.catproof.ai:444','https://app.catproof.ai/?token=secret'):
            with self.assertRaises(collector.LocalError):collector.origin(url)
        self.assertEqual(collector.origin('https://app.catproof.ai/'),'https://app.catproof.ai')
        target=self.root/'target';target.mkdir();self.state.symlink_to(target)
        with self.assertRaises(collector.LocalError):collector.private_dir(self.state)
    def test_enrollment_code_trims_copy_whitespace_and_rejects_invalid_input(self):
        code='fixture_enrollment_code_'+'x'*20
        self.assertEqual(collector.enrollment_code(' \t'+code+'\r\n\u00a0'),code)
        for value in ('', '   ', 'https://app.catproof.ai', 'short', code+' embedded space',None):
            with self.subTest(value_type=type(value).__name__),self.assertRaises(collector.LocalError):collector.enrollment_code(value)
    def test_authentication_errors_are_specific_without_echoing_codes(self):
        from urllib.error import HTTPError
        import io
        cases=[('/api/local/enroll',401,'NEW ten-minute code'),('/api/local/report',401,'Device credential'),('/api/local/enroll',403,'access/origin rejection')]
        for path,status,expected in cases:
            fake=HTTPError('https://app.catproof.ai'+path,status,'Rejected',{},io.BytesIO(b'private server body'))
            with patch.object(collector,'build_opener') as factory:
                factory.return_value.open.side_effect=fake
                with self.assertRaises(collector.LocalError) as caught:collector.request('https://app.catproof.ai',path,{'code':'fixture-secret-code'})
            self.assertIn(expected,str(caught.exception));self.assertNotIn('fixture-secret-code',str(caught.exception));self.assertNotIn('private server body',str(caught.exception))
    def test_cli_enrollment_validates_before_request_and_saves_only_after_success(self):
        import contextlib,io
        code='fixture_enrollment_code_'+'x'*20
        args=['catproof-local.py','--state-dir',str(self.state),'enroll','--server','https://app.catproof.ai']
        with patch.object(collector.sys,'argv',args),patch('builtins.input',return_value='CONNECT'),patch.object(collector.getpass,'getpass',return_value='  '),patch.object(collector,'discover',return_value=[]),patch.object(collector,'request') as send,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(collector.main(),1);send.assert_not_called();self.assertFalse((self.state/'connection.json').exists())
        response={'token':'fixture_device_token_'+'x'*20,'device_id':'fixture-device','expires_at':123456789}
        with patch.object(collector.sys,'argv',args),patch('builtins.input',return_value='CONNECT'),patch.object(collector.getpass,'getpass',return_value=' '+code+'\n'),patch.object(collector,'discover',return_value=[]),patch.object(collector,'request',return_value=response) as send,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(collector.main(),0);send.assert_called_once_with('https://app.catproof.ai','/api/local/enroll',{'code':code},ca_file=None)
        self.assertEqual(collector.config(self.state)['token'],response['token']);self.assertEqual(os.stat(self.state/'connection.json').st_mode&0o777,0o600)
    def test_known_terminal_and_rich_text_paste_wrappers(self):
        code='fixture_enrollment_code_'+'x'*20
        for copied in ('\x1b[200~'+code+'\x1b[201~','\ufeff'+code+'\u200b','`'+code+'`','"'+code+'"',"'"+code+"'"):
            self.assertEqual(collector.enrollment_code(copied),code)
        for copied in ('\x1b[200~'+code,code[:10]+' '+code[10:],'prefix: '+code):
            with self.assertRaises(collector.LocalError) as caught:collector.enrollment_code(copied)
            self.assertIn('characters',str(caught.exception));self.assertNotIn(code,str(caught.exception))
