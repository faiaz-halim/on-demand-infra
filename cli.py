import click
from .deployment_orchestrator import DeploymentOrchestrator, CloudHostedDeploymentHandler
import logging

@click.group()
def cli():
    """Command Line Interface for Deployment Orchestration"""
    pass

@cli.command()
@click.option('--app-name', required=True, help='Name of the application')
@click.option('--image', required=True, help='Docker image name')
def deploy_local(app_name, image):
    """Generate artifacts for local deployment (Docker)"""
    from .security_utils import sanitize_kubernetes_input

    # Sanitize inputs
    sanitized_app_name = sanitize_kubernetes_input(app_name)
    sanitized_image = sanitize_kubernetes_input(image)

    if not sanitized_app_name or not sanitized_image:
        raise click.ClickException("Invalid input detected in application name or image")

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        orchestrator = DeploymentOrchestrator()
        logger.info(f"Generating local deployment artifacts for {sanitized_app_name}")
        artifacts = orchestrator.generate_local_deployment(sanitized_app_name, sanitized_image)

        logger.info("Local deployment artifacts generated successfully")
        click.echo(json.dumps(artifacts, indent=2))
    except Exception as e:
        logger.error(f"Error generating artifacts: {str(e)}")
        raise click.ClickException(f"Artifact generation failed: {str(e)}")

@cli.command()
@click.option('--app-name', required=True, help='Name of the application')
@click.option('--image', required=True, help='Docker image name')
@click.option('--cluster-name', required=True, help='EKS cluster name')
def deploy_cloud_hosted(app_name, image, cluster_name):
    """Deploy application to cloud-hosted EKS cluster"""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        orchestrator = DeploymentOrchestrator()
        handler = CloudHostedDeploymentHandler(orchestrator)

        logger.info(f"Starting cloud-hosted deployment for {app_name}")
        result = handler.execute(app_name, image, cluster_name)

        if 'error' in result:
            logger.error(f"Deployment failed: {result['error']}")
            raise click.ClickException(result['error'])

        logger.info("Deployment completed successfully")
        click.echo(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Deployment error: {str(e)}")
        raise click.ClickException(f"Deployment failed: {str(e)}")

if __name__ == '__main__':
    cli()
