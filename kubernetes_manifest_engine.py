import yaml
from typing import Dict, Any, Optional

class KubernetesManifestEngine:
    """Generates Kubernetes Deployment, Service, and Ingress manifests"""

    @staticmethod
    def generate_deployment(
        app_name: str,
        image: str,
        replicas: int = 1,
        port: int = 8000,
        env_vars: Dict[str, str] = None
    ) -> str:
        """Generate a Kubernetes Deployment manifest"""
        from .security_utils import sanitize_kubernetes_input

        # Sanitize inputs
        sanitized_app_name = sanitize_kubernetes_input(app_name)
        sanitized_image = sanitize_kubernetes_input(image)

        if not sanitized_app_name or not sanitized_image:
            return "# ERROR: Invalid input detected in deployment parameters"

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": f"{sanitized_app_name}-deployment",
                "labels": {
                    "app": sanitized_app_name
                }
            },
            "spec": {
                "replicas": replicas,
                "selector": {
                    "matchLabels": {
                        "app": sanitized_app_name
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": sanitized_app_name
                        }
                    },
                    "spec": {
                        "containers": [{
                            "name": sanitized_app_name,
                            "image": sanitized_image,
                            "ports": [{"containerPort": port}],
                            "env": [{"name": k, "value": v} for k, v in (env_vars or {}).items()]
                        }]
                    }
                }
            }
        }
        return yaml.dump(deployment, sort_keys=False)

    @staticmethod
    def generate_service(
        app_name: str,
        port: int = 8000,
        target_port: int = 8000,
        service_type: str = "ClusterIP"
    ) -> str:
        """Generate a Kubernetes Service manifest"""
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": f"{app_name}-service"
            },
            "spec": {
                "selector": {
                    "app": app_name
                },
                "ports": [{
                    "protocol": "TCP",
                    "port": port,
                    "targetPort": target_port
                }],
                "type": service_type
            }
        }
        return yaml.dump(service, sort_keys=False)

    @staticmethod
    def generate_ingress(
        app_name: str,
        host: str,
        service_name: str,
        service_port: int = 80,
        tls_secret: str = None
    ) -> str:
        """Generate a Kubernetes Ingress manifest for Nginx"""
        from .security_utils import sanitize_kubernetes_input
        app_name = sanitize_kubernetes_input(app_name)
        host = sanitize_kubernetes_input(host)
        service_name = sanitize_kubernetes_input(service_name)
        if tls_secret:
            tls_secret = sanitize_kubernetes_input(tls_secret)

        ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{app_name}-ingress",
                "annotations": {
                    "kubernetes.io/ingress.class": "nginx"
                }
            },
            "spec": {
                "rules": [{
                    "host": host,
                    "http": {
                        "paths": [{
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": service_name,
                                    "port": {
                                        "number": service_port
                                    }
                                }
                            }
                        }]
                    }
                }]
            }
        }

        if tls_secret:
            ingress["spec"]["tls"] = [{
                "hosts": [host],
                "secretName": tls_secret
            }]

        return yaml.dump(ingress, sort_keys=False)
