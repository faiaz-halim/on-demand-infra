import jinja2
import pathlib
import logging
from typing import Dict, Any, List, Optional
import base64

logger = logging.getLogger(__name__)

# Define the template directory relative to this file's location
# Assuming this file is in app/services/ and templates are in app/templates/kubernetes/
TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates" / "kubernetes"

# Initialize Jinja2 environment
try:
    jinja_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False # Explicitly false for YAML/JSON generation
    )
    logger.info(f"Jinja2 environment initialized with template directory: {TEMPLATE_DIR}")
    # You can list templates to verify loading, e.g., logger.debug(jinja_env.list_templates())
except Exception as e:
    logger.error(f"Failed to initialize Jinja2 environment: {e}", exc_info=True)
    jinja_env = None # Ensure it's None if initialization fails


def generate_deployment_manifest(
    image_name: str,
    app_name: str,
    replicas: int = 1,
    env_vars: Optional[Dict[str, str]] = None, # These are plain text values
    secret_name: Optional[str] = None, # Name of the K8s Secret object for envFrom
    ports: Optional[List[int]] = None, # List of container ports to expose
    namespace: str = "default",
    cpu_request: str = "100m",
    memory_request: str = "128Mi",
    cpu_limit: str = "500m",
    memory_limit: str = "512Mi"
) -> str:
    """
    Generates a Kubernetes Deployment manifest using a Jinja2 template.

    Args:
        image_name: The Docker image name (e.g., 'nginx:latest').
        app_name: The name of the application (used for labeling and naming resources).
        replicas: Number of desired replicas.
        env_vars: Dictionary of environment variables (key: value).
                  These will be directly embedded if secret_name is not provided for them.
        secret_name: The name of an existing Kubernetes Secret to source environment variables from.
        ports: A list of container ports to expose (e.g., [80, 8080]).
        namespace: The Kubernetes namespace.
        cpu_request: CPU resource request (e.g., "100m").
        memory_request: Memory resource request (e.g., "128Mi").
        cpu_limit: CPU resource limit.
        memory_limit: Memory resource limit.

    Returns:
        The generated Kubernetes Deployment manifest as a YAML string.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_deployment_manifest.")
        return ""
    try:
        template = jinja_env.get_template("deployment.yaml.j2")
        context = {
            "image_name": image_name,
            "app_name": app_name,
            "replicas": replicas,
            "env_vars": env_vars or {},
            "secret_name": secret_name,
            "ports": ports or [],
            "namespace": namespace,
            "cpu_request": cpu_request,
            "memory_request": memory_request,
            "cpu_limit": cpu_limit,
            "memory_limit": memory_limit,
        }
        rendered_manifest = template.render(context)
        logger.info(f"Deployment manifest generated for app '{app_name}' in namespace '{namespace}'.")
        return rendered_manifest
    except jinja2.TemplateNotFound:
        logger.error(f"Deployment template 'deployment.yaml.j2' not found in {TEMPLATE_DIR}", exc_info=True)
        return ""
    except Exception as e:
        logger.error(f"Error generating deployment manifest for '{app_name}': {e}", exc_info=True)
        return ""


def generate_service_manifest(
    app_name: str,
    service_type: str = "NodePort", # e.g., NodePort, ClusterIP, LoadBalancer
    ports_mapping: Optional[List[Dict[str, int]]] = None, # [{'port': 80, 'targetPort': 8080, 'nodePort': 30080 (optional for NodePort)}]
    namespace: str = "default",
    selector_app_name: Optional[str] = None # Defaults to app_name if None
) -> str:
    """
    Generates a Kubernetes Service manifest using a Jinja2 template.

    Args:
        app_name: The name of the application (used for labeling and naming the service).
        service_type: Type of Kubernetes service (e.g., 'NodePort', 'ClusterIP', 'LoadBalancer').
        ports_mapping: A list of port mappings. Each dict should have 'port' and 'targetPort'.
                       'nodePort' is optional and relevant for NodePort service type.
                       Example: [{'port': 80, 'targetPort': 8080}]
        namespace: The Kubernetes namespace.
        selector_app_name: The app name used for the selector; defaults to app_name.

    Returns:
        The generated Kubernetes Service manifest as a YAML string.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_service_manifest.")
        return ""

    if ports_mapping is None:
        ports_mapping = [] # Default to empty list if not provided

    try:
        template = jinja_env.get_template("service.yaml.j2")
        context = {
            "app_name": app_name,
            "service_type": service_type,
            "ports_mapping": ports_mapping,
            "namespace": namespace,
            "selector_app_name": selector_app_name or app_name,
        }
        rendered_manifest = template.render(context)
        logger.info(f"Service manifest generated for app '{app_name}' in namespace '{namespace}'.")
        return rendered_manifest
    except jinja2.TemplateNotFound:
        logger.error(f"Service template 'service.yaml.j2' not found in {TEMPLATE_DIR}", exc_info=True)
        return ""
    except Exception as e:
        logger.error(f"Error generating service manifest for '{app_name}': {e}", exc_info=True)
        return ""


def generate_secret_manifest(
    secret_name: str,
    data: Dict[str, str], # Plain string data, will be base64 encoded by this function
    namespace: str = "default",
    secret_type: str = "Opaque" # Default Kubernetes secret type
) -> str:
    """
    Generates a Kubernetes Secret manifest using a Jinja2 template.
    Input data values are expected to be plain strings and will be base64 encoded.

    Args:
        secret_name: The name of the Secret.
        data: A dictionary where keys are secret keys and values are plain string data.
        namespace: The Kubernetes namespace.
        secret_type: The type of the Kubernetes Secret (e.g., Opaque, kubernetes.io/dockerconfigjson).

    Returns:
        The generated Kubernetes Secret manifest as a YAML string.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_secret_manifest.")
        return ""

    encoded_data_for_template = {}
    for key, value in (data or {}).items():
        if isinstance(value, str):
            encoded_data_for_template[key] = base64.b64encode(value.encode('utf-8')).decode('utf-8')
        else:
            logger.warning(f"Value for key '{key}' in secret '{secret_name}' is not a string. Skipping encoding and attempting to cast to string.")
            try:
                encoded_data_for_template[key] = base64.b64encode(str(value).encode('utf-8')).decode('utf-8')
            except Exception as e_cast:
                logger.error(f"Could not cast value for key '{key}' to string for base64 encoding: {e_cast}")
                # Skip this key or handle error as per requirements
                continue


    try:
        template = jinja_env.get_template("secret.yaml.j2")
        context = {
            "secret_name": secret_name,
            "data": encoded_data_for_template, # Pass the encoded data to the template under the key 'data'
            "namespace": namespace,
            "secret_type": secret_type,
        }
        rendered_manifest = template.render(context)
        logger.info(f"Secret manifest generated for secret '{secret_name}' in namespace '{namespace}'.")
        return rendered_manifest
    except jinja2.TemplateNotFound:
        logger.error(f"Secret template 'secret.yaml.j2' not found in {TEMPLATE_DIR}", exc_info=True)
        return ""
    except Exception as e:
        logger.error(f"Error generating secret manifest for '{secret_name}': {e}", exc_info=True)
        return ""
