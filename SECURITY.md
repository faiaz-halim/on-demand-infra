# Security Best Practices

## Credential Management

### AWS Credentials
- Use AWS IAM Roles for Service Accounts (IRSA) when running in EKS
- For local development, use AWS SSO or temporary credentials via `aws sso login`
- Never commit AWS access keys to version control
- Rotate credentials regularly (every 90 days maximum)
- Use IAM policies with least privilege

### GitHub Tokens
- Use GitHub App tokens instead of personal access tokens
- Set token expiration to the minimum required duration
- Limit token permissions to only required scopes
- Use fine-grained personal access tokens with minimal permissions

### General Practices
- Store secrets in AWS Secrets Manager or HashiCorp Vault
- Use environment variables for runtime configuration
- Never log credentials or sensitive information
- Use short-lived tokens (1 hour or less) for production workloads
- Regularly audit and rotate all credentials

### Infrastructure as Code (IaC) Security
- Terraform and Kubernetes manifests must never contain hardcoded credentials
- Use IAM roles for EKS service accounts instead of static credentials
- For Terraform, use AWS provider with assumed roles or environment credentials
- All IaC operations should use temporary, scoped credentials
- Secrets in Kubernetes should be injected via Secrets Manager or Vault
