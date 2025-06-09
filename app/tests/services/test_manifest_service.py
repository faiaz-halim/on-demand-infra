import unittest
import yaml # For parsing generated YAML
import base64
import logging
from app.services.manifest_service import (
    generate_deployment_manifest,
    generate_service_manifest,
    generate_secret_manifest,
    TEMPLATE_DIR # To ensure templates are found during tests if Jinja env is re-initialized or checked
)

# Configure basic logging for test visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestManifestService(unittest.TestCase):

    def _assert_metadata(self, manifest_dict, expected_name, expected_namespace, expected_labels=None):
        self.assertIn('metadata', manifest_dict)
        metadata = manifest_dict['metadata']
        self.assertEqual(metadata['name'], expected_name)
        self.assertEqual(metadata['namespace'], expected_namespace)
        if expected_labels:
            self.assertEqual(metadata['labels'], expected_labels)

    def test_generate_secret_manifest(self):
        logger.info("Testing generate_secret_manifest...")
        secret_name = "my-secret"
        namespace = "test-ns"
        original_data = {
            "API_KEY": "supersecretkey",
            "USERNAME": "testuser"
        }

        generated_yaml_string = generate_secret_manifest(secret_name, original_data, namespace)
        self.assertTrue(generated_yaml_string, "Generated YAML string should not be empty")
        # logger.debug(f"Generated Secret YAML:\n{generated_yaml_string}")

        manifest_dict = yaml.safe_load(generated_yaml_string)

        self.assertEqual(manifest_dict['apiVersion'], 'v1')
        self.assertEqual(manifest_dict['kind'], 'Secret')
        self._assert_metadata(manifest_dict, secret_name, namespace)
        self.assertEqual(manifest_dict['type'], 'Opaque')

        self.assertIn('data', manifest_dict)
        expected_api_key_encoded = base64.b64encode(original_data["API_KEY"].encode('utf-8')).decode('utf-8')
        expected_username_encoded = base64.b64encode(original_data["USERNAME"].encode('utf-8')).decode('utf-8')

        self.assertEqual(manifest_dict['data']['API_KEY'], expected_api_key_encoded)
        self.assertEqual(manifest_dict['data']['USERNAME'], expected_username_encoded)
        logger.info("test_generate_secret_manifest passed.")

    def test_generate_deployment_manifest_basic(self):
        logger.info("Testing generate_deployment_manifest_basic...")
        app_name = "my-app"
        image_name = "nginx:latest"
        namespace = "dev"

        generated_yaml_string = generate_deployment_manifest(image_name, app_name, namespace=namespace)
        self.assertTrue(generated_yaml_string, "Generated YAML string should not be empty")
        # logger.debug(f"Generated Basic Deployment YAML:\n{generated_yaml_string}")

        manifest_dict = yaml.safe_load(generated_yaml_string)

        self.assertEqual(manifest_dict['apiVersion'], 'apps/v1')
        self.assertEqual(manifest_dict['kind'], 'Deployment')
        self._assert_metadata(manifest_dict, app_name, namespace, expected_labels={'app': app_name})

        spec = manifest_dict['spec']
        self.assertEqual(spec['replicas'], 1) # Default
        self.assertEqual(spec['selector']['matchLabels']['app'], app_name)

        template_spec = spec['template']['spec']
        container = template_spec['containers'][0]
        self.assertEqual(container['name'], app_name)
        self.assertEqual(container['image'], image_name)

        # Default resources
        self.assertEqual(container['resources']['requests']['cpu'], "100m")
        self.assertEqual(container['resources']['requests']['memory'], "128Mi")
        self.assertEqual(container['resources']['limits']['cpu'], "500m")
        self.assertEqual(container['resources']['limits']['memory'], "512Mi")

        self.assertNotIn('ports', container) # No ports provided
        self.assertNotIn('env', container) # No env_vars or secret_name provided
        self.assertNotIn('envFrom', container)
        logger.info("test_generate_deployment_manifest_basic passed.")

    def test_generate_deployment_manifest_with_all_options(self):
        logger.info("Testing generate_deployment_manifest_with_all_options...")
        app_name = "complex-app"
        image_name = "custom-image:1.2.3"
        namespace = "prod"
        replicas = 3
        env_vars = {"ENV_VAR_1": "value1", "ENV_VAR_2": "value2"}
        secret_name = "app-secrets"
        ports = [8080, 8081]
        cpu_request="200m"
        memory_request="256Mi"
        cpu_limit="1"
        memory_limit="1Gi"

        generated_yaml_string = generate_deployment_manifest(
            image_name, app_name, replicas=replicas, env_vars=env_vars,
            secret_name=secret_name, ports=ports, namespace=namespace,
            cpu_request=cpu_request, memory_request=memory_request,
            cpu_limit=cpu_limit, memory_limit=memory_limit
        )
        self.assertTrue(generated_yaml_string, "Generated YAML string should not be empty")
        # logger.debug(f"Generated Full Deployment YAML:\n{generated_yaml_string}")

        manifest_dict = yaml.safe_load(generated_yaml_string)
        self.assertEqual(manifest_dict['apiVersion'], 'apps/v1')
        self.assertEqual(manifest_dict['kind'], 'Deployment')
        self._assert_metadata(manifest_dict, app_name, namespace, expected_labels={'app': app_name})

        spec = manifest_dict['spec']
        self.assertEqual(spec['replicas'], replicas)

        container = spec['template']['spec']['containers'][0]
        self.assertEqual(container['name'], app_name)
        self.assertEqual(container['image'], image_name)

        # Ports
        self.assertEqual(len(container['ports']), len(ports))
        for i, port_val in enumerate(ports):
            self.assertEqual(container['ports'][i]['containerPort'], port_val)

        # Env Vars & EnvFrom
        self.assertIn('env', container)
        self.assertIn('envFrom', container)

        # Check direct env vars
        expected_env_list = [{'name': k, 'value': v} for k,v in env_vars.items()]
        # The order might not be guaranteed, so check subset or sort
        for expected_item in expected_env_list:
            self.assertIn(expected_item, container['env'])

        # Check envFrom secretRef
        self.assertEqual(container['envFrom'][0]['secretRef']['name'], secret_name)

        # Custom resources
        self.assertEqual(container['resources']['requests']['cpu'], cpu_request)
        self.assertEqual(container['resources']['requests']['memory'], memory_request)
        self.assertEqual(container['resources']['limits']['cpu'], cpu_limit)
        self.assertEqual(container['resources']['limits']['memory'], memory_limit)
        logger.info("test_generate_deployment_manifest_with_all_options passed.")

    def test_generate_service_manifest_nodeport(self):
        logger.info("Testing test_generate_service_manifest_nodeport...")
        app_name = "nodeport-app"
        namespace = "services"
        ports_mapping = [
            {'name': 'http', 'port': 80, 'targetPort': 8080, 'nodePort': 30080},
            {'port': 443, 'targetPort': 8443, 'nodePort': 30443, 'protocol': 'TCP'} # protocol specified
        ]

        generated_yaml_string = generate_service_manifest(
            app_name, service_type="NodePort", ports_mapping=ports_mapping, namespace=namespace
        )
        self.assertTrue(generated_yaml_string, "Generated YAML string should not be empty")
        # logger.debug(f"Generated NodePort Service YAML:\n{generated_yaml_string}")

        manifest_dict = yaml.safe_load(generated_yaml_string)
        self.assertEqual(manifest_dict['apiVersion'], 'v1')
        self.assertEqual(manifest_dict['kind'], 'Service')
        self._assert_metadata(manifest_dict, app_name, namespace)

        spec = manifest_dict['spec']
        self.assertEqual(spec['type'], "NodePort")
        self.assertEqual(spec['selector']['app'], app_name)

        self.assertEqual(len(spec['ports']), 2)

        port1 = spec['ports'][0]
        self.assertEqual(port1['name'], 'http')
        self.assertEqual(port1['port'], 80)
        self.assertEqual(port1['targetPort'], 8080)
        self.assertEqual(port1['nodePort'], 30080)
        self.assertEqual(port1['protocol'], 'TCP') # Default

        port2 = spec['ports'][1]
        self.assertEqual(port2['name'], 'http-443') # Default name generation
        self.assertEqual(port2['port'], 443)
        self.assertEqual(port2['targetPort'], 8443)
        self.assertEqual(port2['nodePort'], 30443)
        self.assertEqual(port2['protocol'], 'TCP') # Explicitly TCP
        logger.info("test_generate_service_manifest_nodeport passed.")

    def test_generate_service_manifest_clusterip(self):
        logger.info("Testing test_generate_service_manifest_clusterip...")
        app_name = "clusterip-app"
        namespace = "internal"
        ports_mapping = [
            {'port': 5000, 'targetPort': 5001} # nodePort should be ignored
        ]

        generated_yaml_string = generate_service_manifest(
            app_name, service_type="ClusterIP", ports_mapping=ports_mapping, namespace=namespace
        )
        self.assertTrue(generated_yaml_string, "Generated YAML string should not be empty")
        # logger.debug(f"Generated ClusterIP Service YAML:\n{generated_yaml_string}")

        manifest_dict = yaml.safe_load(generated_yaml_string)
        self.assertEqual(manifest_dict['spec']['type'], "ClusterIP")

        port1 = manifest_dict['spec']['ports'][0]
        self.assertEqual(port1['name'], 'http-5000') # Default name
        self.assertEqual(port1['port'], 5000)
        self.assertEqual(port1['targetPort'], 5001)
        self.assertNotIn('nodePort', port1, "nodePort should not be present for ClusterIP services")
        logger.info("test_generate_service_manifest_clusterip passed.")

if __name__ == '__main__':
    # This allows running the tests directly from this file
    # Ensure that the Jinja2 environment in manifest_service can find the templates.
    # This might require setting PYTHONPATH or ensuring the test runner handles it.
    # For simplicity, we assume manifest_service.TEMPLATE_DIR is correctly pointing.
    if not TEMPLATE_DIR.exists():
        logger.error(f"TEMPLATE_DIR {TEMPLATE_DIR} does not exist. Tests might fail to find templates.")

    unittest.main()
