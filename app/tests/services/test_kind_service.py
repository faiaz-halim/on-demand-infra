import unittest
from unittest.mock import patch, MagicMock, mock_open
import subprocess
import os

# Adjust the import path based on your project structure
# Assuming your tests are in 'app/tests' and services in 'app/services'
from app.services.kind_service import (
    detect_kind_cluster,
    create_kind_cluster,
    apply_calico,
    delete_kind_cluster,
    load_image_into_kind, # Added import
    _run_command
)

# If app.core.config.settings is used directly in kind_service for defaults (it's not currently)
# from app.core.config import Settings

class TestKindService(unittest.TestCase):

    @patch('app.services.kind_service.shutil.which', return_value='/fake/path/to/kind')
    @patch('app.services.kind_service._run_command')
    def test_detect_kind_cluster_found(self, mock_run_command, mock_shutil_which):
        mock_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_process.returncode = 0
        mock_process.stdout = "test-cluster\nanother-cluster\n"
        mock_run_command.return_value = mock_process

        self.assertTrue(detect_kind_cluster("test-cluster"))
        mock_run_command.assert_called_once_with([mock_shutil_which.return_value, 'get', 'clusters'])
        mock_shutil_which.assert_called_once_with('kind')


    @patch('app.services.kind_service.shutil.which', return_value='/fake/path/to/kind')
    @patch('app.services.kind_service._run_command')
    def test_detect_kind_cluster_not_found(self, mock_run_command, mock_shutil_which):
        mock_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_process.returncode = 0
        mock_process.stdout = "another-cluster\nyet-another-cluster\n"
        mock_run_command.return_value = mock_process

        self.assertFalse(detect_kind_cluster("test-cluster"))
        mock_run_command.assert_called_once_with([mock_shutil_which.return_value, 'get', 'clusters'])
        mock_shutil_which.assert_called_once_with('kind')

    @patch('app.services.kind_service.shutil.which', return_value='/fake/path/to/kind')
    @patch('app.services.kind_service._run_command')
    def test_detect_kind_cluster_subprocess_error(self, mock_run_command, mock_shutil_which):
        mock_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_process.returncode = 1
        mock_process.stderr = "Some kind error"
        mock_run_command.return_value = mock_process

        self.assertFalse(detect_kind_cluster("test-cluster"))
        mock_run_command.assert_called_once_with([mock_shutil_which.return_value, 'get', 'clusters'])
        mock_shutil_which.assert_called_once_with('kind')

    @patch('app.services.kind_service.shutil.which', return_value=None) # Kind not found
    @patch('app.services.kind_service._run_command')
    def test_detect_kind_cluster_kind_not_found(self, mock_run_command, mock_shutil_which):
        self.assertFalse(detect_kind_cluster("test-cluster"))
        mock_shutil_which.assert_called_once_with('kind')
        mock_run_command.assert_not_called()


    @patch('app.services.kind_service.detect_kind_cluster')
    @patch('app.services.kind_service.apply_calico')
    @patch('app.services.kind_service._run_command')
    @patch('shutil.which', return_value='/fake/path/to/kind')
    def test_create_kind_cluster_success_no_calico(self, mock_shutil_which_kind_exe, mock_run_command_create, mock_apply_calico, mock_detect_cluster):
        mock_detect_cluster.return_value = False

        mock_create_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_create_process.returncode = 0
        mock_create_process.stdout = "Cluster created"
        mock_run_command_create.return_value = mock_create_process

        self.assertTrue(create_kind_cluster("new-cluster", calico_yaml_url=None))
        mock_detect_cluster.assert_called_once_with("new-cluster")
        mock_run_command_create.assert_called_once_with([mock_shutil_which_kind_exe.return_value, 'create', 'cluster', '--name', 'new-cluster'])
        mock_apply_calico.assert_not_called()
        mock_shutil_which_kind_exe.assert_called_with('kind')


    @patch('app.services.kind_service.detect_kind_cluster')
    @patch('app.services.kind_service.apply_calico', return_value=True)
    @patch('app.services.kind_service._run_command')
    @patch('shutil.which', return_value='/fake/path/to/kind')
    def test_create_kind_cluster_success_with_calico(self, mock_shutil_which_kind_exe, mock_run_command_create, mock_apply_calico, mock_detect_cluster):
        mock_detect_cluster.return_value = False

        mock_create_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_create_process.returncode = 0
        mock_create_process.stdout = "Cluster created"
        mock_run_command_create.return_value = mock_create_process

        self.assertTrue(create_kind_cluster("new-cluster", calico_yaml_url="http://calico.yaml"))
        mock_detect_cluster.assert_called_once_with("new-cluster")
        mock_run_command_create.assert_called_once_with([mock_shutil_which_kind_exe.return_value, 'create', 'cluster', '--name', 'new-cluster'])
        mock_apply_calico.assert_called_once_with("new-cluster", "http://calico.yaml")
        mock_shutil_which_kind_exe.assert_called_with('kind')


    @patch('app.services.kind_service.detect_kind_cluster', return_value=True)
    @patch('app.services.kind_service.apply_calico', return_value=True)
    @patch('app.services.kind_service._run_command') # For create_kind_cluster itself
    @patch('shutil.which', return_value='/fake/path/to/kind') # For create_kind_cluster's kind check
    def test_create_kind_cluster_already_exists_with_calico_apply(self, mock_shutil_which_create, mock_run_command_create, mock_apply_calico, mock_detect_cluster):
        # apply_calico will have its own shutil.which calls, so we need to account for them if not mocking apply_calico deeply
        self.assertTrue(create_kind_cluster("existing-cluster", calico_yaml_url="http://calico.yaml"))
        mock_detect_cluster.assert_called_once_with("existing-cluster")
        mock_run_command_create.assert_not_called()
        mock_apply_calico.assert_called_once_with("existing-cluster", "http://calico.yaml")


    @patch('app.services.kind_service.detect_kind_cluster', return_value=False)
    @patch('app.services.kind_service._run_command')
    @patch('shutil.which', return_value='/fake/path/to/kind')
    def test_create_kind_cluster_failure_on_create(self, mock_shutil_which_kind_exe, mock_run_command_create, mock_detect_cluster):
        mock_create_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_create_process.returncode = 1
        mock_create_process.stderr = "Kind create failed"
        mock_run_command_create.return_value = mock_create_process

        self.assertFalse(create_kind_cluster("new-cluster"))
        mock_run_command_create.assert_called_once_with([mock_shutil_which_kind_exe.return_value, 'create', 'cluster', '--name', 'new-cluster'])
        mock_shutil_which_kind_exe.assert_called_with('kind')


    @patch('app.services.kind_service.detect_kind_cluster', return_value=False)
    @patch('app.services.kind_service.apply_calico', return_value=False)
    @patch('app.services.kind_service._run_command') # For kind create
    @patch('app.services.kind_service.delete_kind_cluster')
    @patch('shutil.which', return_value='/fake/path/to/kind') # For kind create
    def test_create_kind_cluster_failure_on_calico(self, mock_shutil_which_create, mock_delete_cluster, mock_run_command_create, mock_apply_calico, mock_detect_cluster):
        mock_create_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_create_process.returncode = 0
        mock_create_process.stdout = "Cluster created"
        mock_run_command_create.return_value = mock_create_process

        self.assertFalse(create_kind_cluster("new-cluster", calico_yaml_url="http://calico.yaml"))
        mock_apply_calico.assert_called_once_with("new-cluster", "http://calico.yaml")
        mock_delete_cluster.assert_called_once_with("new-cluster")


    @patch('shutil.which') # Patched here for all calls within apply_calico
    @patch('app.services.kind_service._run_command')
    @patch('tempfile.NamedTemporaryFile')
    @patch('os.remove')
    def test_apply_calico_success(self, mock_os_remove, mock_tempfile, mock_run_command, mock_shutil_which):
        # Side effect for shutil.which to return different paths for kind and kubectl
        def shutil_which_side_effect(cmd):
            if cmd == 'kind': return '/fake/path/to/kind'
            if cmd == 'kubectl': return '/fake/path/to/kubectl'
            return None
        mock_shutil_which.side_effect = shutil_which_side_effect

        mock_kubeconfig_export_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_kubeconfig_export_proc.returncode = 0
        mock_kubeconfig_export_proc.stdout = "kubeconfig-data"

        mock_kubectl_apply_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_kubectl_apply_proc.returncode = 0
        mock_kubectl_apply_proc.stdout = "calico applied"

        mock_run_command.side_effect = [mock_kubeconfig_export_proc, mock_kubectl_apply_proc]

        mock_temp_file_obj = MagicMock()
        mock_temp_file_obj.name = "/tmp/fake_kubeconfig.yaml"
        mock_tempfile_cm = MagicMock() # Mock for the context manager __enter__
        mock_tempfile_cm.__enter__.return_value = mock_temp_file_obj
        mock_tempfile.return_value = mock_tempfile_cm # mock_tempfile is the class itself

        self.assertTrue(apply_calico("test-cluster", "http://calico.yaml"))

        expected_export_cmd = ['/fake/path/to/kind', 'export', 'kubeconfig', '--name', 'test-cluster']
        expected_apply_cmd = ['/fake/path/to/kubectl', 'apply', '-f', 'http://calico.yaml', '--kubeconfig', mock_temp_file_obj.name]

        mock_run_command.assert_any_call(expected_export_cmd)
        mock_run_command.assert_any_call(expected_apply_cmd)
        mock_temp_file_obj.write.assert_called_once_with("kubeconfig-data")
        mock_os_remove.assert_called_once_with(mock_temp_file_obj.name)
        self.assertEqual(mock_shutil_which.call_count, 2) # Called for kubectl and kind

    @patch('shutil.which', side_effect=lambda cmd: None if cmd == 'kubectl' else f'/fake/path/to/{cmd}')
    @patch('app.services.kind_service._run_command')
    def test_apply_calico_kubectl_not_found(self, mock_run_command, mock_shutil_which):
        self.assertFalse(apply_calico("test-cluster", "http://calico.yaml"))
        mock_shutil_which.assert_any_call('kubectl')
        mock_run_command.assert_not_called() # Should not proceed to run commands

    @patch('shutil.which')
    @patch('app.services.kind_service._run_command')
    def test_apply_calico_kubeconfig_export_failure(self, mock_run_command, mock_shutil_which):
        def shutil_which_side_effect(cmd):
            if cmd == 'kind': return '/fake/path/to/kind'
            if cmd == 'kubectl': return '/fake/path/to/kubectl'
            return None
        mock_shutil_which.side_effect = shutil_which_side_effect

        mock_kubeconfig_export_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_kubeconfig_export_proc.returncode = 1
        mock_kubeconfig_export_proc.stderr = "Failed to export"
        mock_run_command.return_value = mock_kubeconfig_export_proc

        self.assertFalse(apply_calico("test-cluster", "http://calico.yaml"))
        mock_run_command.assert_called_once_with(['/fake/path/to/kind', 'export', 'kubeconfig', '--name', 'test-cluster'])

    @patch('shutil.which')
    @patch('app.services.kind_service._run_command')
    @patch('tempfile.NamedTemporaryFile')
    @patch('os.remove')
    def test_apply_calico_kubectl_apply_failure(self, mock_os_remove, mock_tempfile, mock_run_command, mock_shutil_which):
        def shutil_which_side_effect(cmd):
            if cmd == 'kind': return '/fake/path/to/kind'
            if cmd == 'kubectl': return '/fake/path/to/kubectl'
            return None
        mock_shutil_which.side_effect = shutil_which_side_effect

        mock_kubeconfig_export_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_kubeconfig_export_proc.returncode = 0
        mock_kubeconfig_export_proc.stdout = "kubeconfig-data"

        mock_kubectl_apply_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_kubectl_apply_proc.returncode = 1
        mock_kubectl_apply_proc.stderr = "kubectl apply failed"

        mock_run_command.side_effect = [mock_kubeconfig_export_proc, mock_kubectl_apply_proc]

        mock_temp_file_obj = MagicMock()
        mock_temp_file_obj.name = "/tmp/fake_kubeconfig.yaml"
        mock_tempfile_cm = MagicMock()
        mock_tempfile_cm.__enter__.return_value = mock_temp_file_obj
        mock_tempfile.return_value = mock_tempfile_cm

        self.assertFalse(apply_calico("test-cluster", "http://calico.yaml"))
        mock_os_remove.assert_called_once_with(mock_temp_file_obj.name)


    @patch('app.services.kind_service.detect_kind_cluster', return_value=True)
    @patch('app.services.kind_service._run_command')
    @patch('shutil.which', return_value='/fake/path/to/kind')
    def test_delete_kind_cluster_success(self, mock_shutil_which_kind_exe, mock_run_command, mock_detect_cluster):
        mock_delete_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_delete_proc.returncode = 0
        mock_run_command.return_value = mock_delete_proc

        self.assertTrue(delete_kind_cluster("test-cluster"))
        mock_detect_cluster.assert_called_once_with("test-cluster")
        mock_run_command.assert_called_once_with([mock_shutil_which_kind_exe.return_value, 'delete', 'cluster', '--name', 'test-cluster'])
        mock_shutil_which_kind_exe.assert_called_with('kind') # Check kind is sought for delete

    @patch('app.services.kind_service.detect_kind_cluster', return_value=True)
    @patch('app.services.kind_service._run_command')
    @patch('shutil.which', return_value='/fake/path/to/kind')
    def test_delete_kind_cluster_failure(self, mock_shutil_which_kind_exe, mock_run_command, mock_detect_cluster):
        mock_delete_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_delete_proc.returncode = 1
        mock_delete_proc.stderr = "Kind delete failed"
        mock_run_command.return_value = mock_delete_proc

        self.assertFalse(delete_kind_cluster("test-cluster"))
        mock_run_command.assert_called_once_with([mock_shutil_which_kind_exe.return_value, 'delete', 'cluster', '--name', 'test-cluster'])

    @patch('app.services.kind_service.detect_kind_cluster', return_value=False)
    @patch('app.services.kind_service._run_command') # Should not be called
    @patch('shutil.which', return_value='/fake/path/to/kind') # For the initial check in delete_kind_cluster
    def test_delete_kind_cluster_not_exists(self, mock_shutil_which_kind_exe, mock_run_command_delete, mock_detect_cluster):
        # shutil.which in delete_kind_cluster is called before detect_kind_cluster in current impl.
        # detect_kind_cluster also calls shutil.which. So, multiple calls.
        self.assertTrue(delete_kind_cluster("test-cluster"))
        mock_detect_cluster.assert_called_once_with("test-cluster")
        mock_run_command_delete.assert_not_called()


    @patch('shutil.which', return_value='/fake/path/to/kind')
    @patch('app.services.kind_service._run_command')
    def test_load_image_into_kind_success(self, mock_run_command, mock_shutil_which):
        mock_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_process.returncode = 0
        mock_process.stdout = "Image loaded"
        mock_run_command.return_value = mock_process

        self.assertTrue(load_image_into_kind("mytestimage:latest", "test-cluster"))
        expected_cmd = [mock_shutil_which.return_value, 'load', 'docker-image', "mytestimage:latest", '--name', 'test-cluster']
        mock_run_command.assert_called_once_with(expected_cmd)
        mock_shutil_which.assert_called_once_with('kind')

    @patch('shutil.which', return_value='/fake/path/to/kind')
    @patch('app.services.kind_service._run_command')
    def test_load_image_into_kind_failure(self, mock_run_command, mock_shutil_which):
        mock_process = MagicMock(spec=subprocess.CompletedProcess)
        mock_process.returncode = 1
        mock_process.stderr = "Kind load image failed"
        mock_run_command.return_value = mock_process

        self.assertFalse(load_image_into_kind("myimage:latest", "test-cluster"))
        expected_cmd = [mock_shutil_which.return_value, 'load', 'docker-image', "myimage:latest", '--name', 'test-cluster']
        mock_run_command.assert_called_once_with(expected_cmd)
        mock_shutil_which.assert_called_once_with('kind')

    @patch('shutil.which', return_value=None) # kind CLI not found
    @patch('app.services.kind_service._run_command')
    def test_load_image_into_kind_cli_not_found(self, mock_run_command, mock_shutil_which):
        self.assertFalse(load_image_into_kind("myimage:latest", "test-cluster"))
        mock_shutil_which.assert_called_once_with('kind')
        mock_run_command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
