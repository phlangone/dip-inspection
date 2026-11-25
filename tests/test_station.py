"""Hardware-independent tests for configuration, state, and worker supervision."""
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from dip_inspection.core.application_state import ApplicationState
from dip_inspection.core.configuration import DEFAULTS, atomic_json, validate_products, validate_settings
from dip_inspection.core.inspection_worker import Cycle
from dip_inspection.core.supervisor import Supervisor, create_app
from tests.fakes import idle_worker, crash_worker, hardware_worker, unsafe_worker
from dip_inspection.integrations.wifi_manager import WifiManager

PRODUCTS={"reject_destination":5,"products":[{"part_number":"TEST123N","destination":1,"enabled":True}]}


def wait_until(predicate, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.03)
    raise AssertionError('Condição não atingida no prazo')


class ConfigTests(unittest.TestCase):
    def test_conflicts_and_protocol(self):
        duplicate=copy.deepcopy(PRODUCTS);duplicate['products'].append({"part_number":"OTHER1","destination":1,"enabled":True})
        with self.assertRaises(ValueError): validate_products(duplicate)
        duplicate['products'][1]['enabled']=False;validate_products(duplicate)
        with self.assertRaises(ValueError): validate_products({**PRODUCTS,'reject_destination':4})
        with self.assertRaises(ValueError): validate_settings({'robot':{'host':'not-ip'}})

    def test_atomic_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'products.json';atomic_json(path,PRODUCTS);original=path.read_bytes()
            with patch('dip_inspection.core.configuration.os.replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError):atomic_json(path,{'products':[]})
            self.assertEqual(original,path.read_bytes())

    def test_recovery_and_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=ApplicationState(root)
            cycle=Cycle();cycle.ocr={'identified':True,'part_number':'TEST123N','status':'IDENTIFICADO','best':{}}
            cycle.product={'destination':1};cycle.side_a=cycle.side_b={'anomaly':False,'status':'NORMAL'}
            destination,approved=cycle.destination();self.assertTrue(approved);self.assertEqual(destination,1)
            item=cycle.history(destination,approved);state.record(item);state.record(item)
            self.assertEqual(state.snapshot()['total_processed'],1)
            self.assertEqual(ApplicationState(root).snapshot()['total_processed'],1)
            cycle=Cycle();state.record(cycle.history(5,False))
            self.assertEqual(state.snapshot()['total_rejected'],1)
            self.assertEqual(state.snapshot()['approved_count_by_part_number']['TEST123N'],1)
            state.path.write_text('corrupt');recovered=ApplicationState(root)
            self.assertTrue(recovered.production_valid);self.assertEqual(recovered.snapshot()['total_processed'],1)
            self.assertIn('backup',recovered.snapshot()['last_error'])
            recovered.update(safety_latched=True)
            self.assertTrue(ApplicationState(root).snapshot()['safety_latched'])
            recovered.reset()
            self.assertTrue(recovered.snapshot()['safety_latched'])

    def test_malformed_production_does_not_crash_supervisor_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            from dip_inspection.core.application_state import empty_production
            bad=empty_production();bad['recent_history']=[None]
            atomic_json(root/'production_state.json',bad)
            result=ApplicationState(root)
            self.assertFalse(result.production_valid)
            self.assertEqual(result.snapshot()['station_status'],'STOPPED')

    def test_safety_disk_failure_keeps_latch(self):
        with tempfile.TemporaryDirectory() as folder:
            state=ApplicationState(Path(folder))
            with patch('dip_inspection.core.application_state.atomic_json',side_effect=OSError('disk')):
                state.update(safety_latched=True)
            self.assertTrue(state.snapshot()['safety_latched'])
            self.assertFalse(state.production_valid)

    def test_unsafe_classification(self):
        c=Cycle();c.product={'destination':1};c.side_a=c.side_b={'anomaly':False}
        self.assertEqual(c.destination(),(5,False));c.ocr={'identified':True};self.assertEqual(c.destination(),(1,True))
        c.failed=True;self.assertEqual(c.destination(),(5,False))
        c.failed=False;c.side_a={'anomaly':True};self.assertEqual(c.destination(),(5,False))


class StationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        atomic_json(self.root/'products.json',PRODUCTS)
        settings=copy.deepcopy(DEFAULTS);settings['application']['auto_recovery']=False
        atomic_json(self.root/'settings.json',settings)
        self.supervisor=Supervisor(self.root,worker_target=idle_worker)
        self.app=create_app(self.supervisor,generate_password_hash('test-password'))
        self.client=self.app.test_client();self.token=self.client.get('/api/session').json['csrf']

    def tearDown(self):self.supervisor.close();self.temp.cleanup()
    def post(self,path,data=None):return self.client.post(path,json=data or {},headers={'X-CSRF-Token':self.token})
    def login(self):
        result=self.post('/api/login',{'password':'test-password'})
        self.assertEqual(result.status_code,200);self.token=result.json['csrf']

    def test_authentication_csrf_and_secret(self):
        self.assertEqual(self.post('/api/system/start').status_code,401)
        self.assertEqual(self.client.post('/api/system/start',json={}).status_code,403);self.login()
        self.assertEqual(self.client.post('/api/system/start',json={}).status_code,403)
        self.supervisor.config.save_settings({'mqtt':{'password':'secret-value'}})
        settings=self.client.get('/api/settings').json
        self.assertNotIn('password',settings['mqtt']);self.assertTrue(settings['mqtt']['password_configured'])
        self.supervisor.log('message secret-value');self.assertNotIn('secret-value',self.client.get('/api/system/logs').text)

    def test_start_stop_restart_flask_survives(self):
        self.login();self.assertEqual(self.post('/api/system/start').status_code,200)
        wait_until(lambda:self.supervisor.state.snapshot()['station_status']=='WAITING_PART');first=self.supervisor.proc.pid
        self.assertEqual(self.post('/api/system/stop',{'confirmed':True}).status_code,200)
        self.assertEqual(self.client.get('/api/state').status_code,200)
        self.assertEqual(self.client.get('/api/state').json['station_status'],'STOPPED')
        self.assertEqual(self.post('/api/system/start').status_code,200);self.assertNotEqual(first,self.supervisor.proc.pid)
        self.assertEqual(self.post('/api/system/restart',{'confirmed':True}).status_code,200)
        self.assertEqual(self.client.get('/api/state').status_code,200)

    def test_products_settings_reset(self):
        self.login();self.assertEqual(self.post('/api/products',PRODUCTS).status_code,200)
        self.assertEqual(self.post('/api/settings',{'confirmed':True,'robot':{'host':'bad'}}).status_code,400)
        self.assertEqual(self.post('/api/system/reset-production').status_code,400)
        self.assertEqual(self.post('/api/system/reset-production',{'confirmed':True}).status_code,200)
        self.supervisor.start();self.assertEqual(self.post('/api/settings',{'confirmed':True}).status_code,400)

    def test_worker_crash_limited_recovery(self):
        self.supervisor.target=crash_worker
        self.supervisor.config.save_settings({'application':{'auto_recovery':True,'restart_limit':2}});self.supervisor.start()
        wait_until(lambda:self.supervisor.state.snapshot()['station_status']=='FAULT',timeout=12)
        self.assertEqual(len(self.supervisor.attempts),2);self.assertEqual(self.client.get('/api/state').status_code,200)
        self.assertFalse(self.supervisor.running())

    def prepare_models(self):
        (self.root/'models').mkdir()
        for name in ('side_a.eim','side_b.eim'):(self.root/'models'/name).write_text('TEST MOCK ONLY')
        (self.root/'empty_reference.jpg').write_text('TEST MOCK ONLY')

    def test_real_worker_orchestration_mocked_hardware(self):
        self.prepare_models();self.supervisor.target=hardware_worker;self.supervisor.start()
        wait_until(lambda:self.supervisor.state.snapshot()['total_processed']==1)
        result=self.supervisor.state.snapshot();self.assertEqual(result['total_approved'],1);self.assertEqual(result['destination'],1)
        self.supervisor.stop();self.assertEqual(self.supervisor.state.snapshot()['total_processed'],1)
        self.supervisor.start();wait_until(lambda:self.supervisor.state.snapshot()['total_processed']==2)
        self.assertEqual(self.supervisor.state.snapshot()['total_approved'],2)

    def test_safety_prevents_auto_restart(self):
        self.prepare_models();self.supervisor.target=unsafe_worker
        self.supervisor.config.save_settings({'application':{'auto_recovery':True}});self.supervisor.start()
        wait_until(lambda:self.supervisor.state.snapshot()['station_status']=='FAULT')
        self.assertTrue(self.supervisor.state.snapshot()['safety_latched']);self.assertEqual(len(self.supervisor.attempts),0)
        with self.assertRaises(ValueError):self.supervisor.start()
        self.assertEqual(self.client.get('/api/state').status_code,200)

    def test_unknown_and_low_confidence_reject(self):
        from tests.fakes import unknown_worker, low_confidence_worker
        self.prepare_models()
        for target in (unknown_worker, low_confidence_worker):
            self.supervisor.target=target;self.supervisor.start()
            previous=self.supervisor.state.snapshot()['total_processed']
            wait_until(lambda:self.supervisor.state.snapshot()['total_processed']==previous+1)
            result=self.supervisor.state.snapshot()
            self.assertEqual(result['destination'],5);self.assertEqual(result['total_approved'],0)
            self.assertEqual(result['approved_count_by_part_number'],{})
            self.supervisor.stop()

    def test_camera_and_rtde_loss_and_late_command(self):
        from tests.fakes import broken_camera_worker, lost_rtde_worker, late_request_worker
        self.prepare_models()
        for target in (broken_camera_worker,lost_rtde_worker,late_request_worker):
            self.supervisor.target=target;self.supervisor.start()
            wait_until(lambda:self.supervisor.state.snapshot()['station_status']=='FAULT')
            self.assertEqual(self.client.get('/api/state').status_code,200)
            self.assertEqual(self.supervisor.state.snapshot()['total_processed'],0)
            self.supervisor.stop()

    def test_pages_local_assets_and_bad_request(self):
        for page in ('/','/products','/settings','/system'):
            response=self.client.get(page)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('https://',response.text)
        self.login()
        response=self.client.post('/api/products',data='[]',content_type='application/json',headers={'X-CSRF-Token':self.token})
        self.assertEqual(response.status_code,400)

    def test_missing_hardware_keeps_ui(self):
        from dip_inspection.core.inspection_worker import worker_main
        self.supervisor.target=worker_main;self.supervisor.start()
        wait_until(lambda:self.supervisor.state.snapshot()['station_status']=='FAULT')
        self.assertEqual(self.client.get('/api/state').status_code,200)


class WifiTests(unittest.TestCase):
    def test_route_to_robot_blocks_wifi_change(self):
        manager=WifiManager(lambda:'192.168.15.51')
        with patch.object(manager,'interface',return_value='wlan0'),patch('dip_inspection.integrations.wifi_manager.subprocess.run') as run:
            run.return_value.returncode=0;run.return_value.stdout='[{"dev":"wlan0"}]'
            with self.assertRaises(ValueError):manager.change('disconnect',{})
            self.assertEqual(run.call_count,1)

    def test_only_wifi_interface_command(self):
        manager=WifiManager(lambda:'192.168.15.51');commands=[]
        with patch.object(manager,'interface',return_value='wlan0'),patch.object(manager,'_check_robot_route'),patch.object(manager,'_run',side_effect=lambda args:commands.append(args) or ''):
            manager.change('disconnect',{})
        self.assertEqual(commands,[['device','disconnect','wlan0']])

class WifiHelperTests(unittest.TestCase):
    def setUp(self):
        import importlib.machinery, importlib.util
        path=Path(__file__).resolve().parents[1]/'deployment/futurecom-wifi'
        loader=importlib.machinery.SourceFileLoader('wifi_helper',str(path))
        spec=importlib.util.spec_from_loader(loader.name,loader)
        self.helper=importlib.util.module_from_spec(spec);loader.exec_module(self.helper)

    def call_helper(self,args):
        import io,json
        request={'args':args,'robot_host':'192.168.15.51'}
        def nm(args):
            if args==['-g','GENERAL.TYPE','device','show','wlan0']:return 'wifi'
            if 'connection.type' in args:return '802-11-wireless'
            if 'connection.interface-name' in args:return 'wlan0'
            if 'connection.id' in args:return 'futurecom-wifi-12345678'
            return 'OK'
        with patch.object(self.helper.os,'geteuid',return_value=0),patch.dict(self.helper.os.environ,{'SUDO_UID':'1000'}),patch.object(self.helper.sys,'argv',['helper']),patch.object(self.helper.sys,'stdin',io.StringIO(json.dumps(request))),patch('builtins.open',return_value=io.StringIO('wlan0')),patch.object(self.helper,'nm',side_effect=nm),patch.object(self.helper.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout='[{"dev":"eth0"}]'
            return self.helper.main()

    def test_helper_accepts_wifi_disconnect_and_add(self):
        self.assertEqual(self.call_helper(['device','disconnect','wlan0']),'OK')
        profile='12345678-1234-1234-1234-123456789012'
        args=['connection','add','type','wifi','ifname','wlan0','con-name','futurecom-wifi-12345678','ssid','LAB','connection.uuid',profile,'connection.autoconnect','no','ipv4.method','auto','ipv4.never-default','yes','ipv6.method','auto','ipv6.never-default','yes']
        self.assertEqual(self.call_helper(args),'OK')

    def test_helper_rejects_ethernet_and_arbitrary_properties(self):
        for args in (['device','disconnect','eth0'],['networking','off'],['connection','modify','uuid','12345678-1234-1234-1234-123456789012','ipv4.addresses','1.2.3.4/24']):
            with self.assertRaises(ValueError):self.call_helper(args)
